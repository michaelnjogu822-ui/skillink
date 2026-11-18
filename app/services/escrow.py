"""Escrow payments for marketplace projects.

Money moves in three steps, and every step is a state change on one
``transactions`` row:

1. **fund** - the client's money leaves their account and is held by the
   platform: status ``held``.
2. **release** - the client approves the work, so the held money is paid to
   the freelancer: status ``released``.
3. **refund** - the project is cancelled before approval, so the held money
   returns to the client: status ``refunded``.

Routes never talk to a payment provider directly. They call this service,
which calls a :class:`PaymentProvider`. That is the seam where a real
provider such as Safaricom M-Pesa Daraja will be plugged in later: implement
the three methods, register the class in ``PROVIDERS``, and set
``PAYMENT_PROVIDER``. No route or template has to change.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from flask import current_app, url_for

from app.extensions import db
from app.models import Project, Transaction
from app.services import notifications


class PaymentError(Exception):
    """A payment transition that the project's current state does not allow."""


@dataclass(frozen=True)
class PaymentReceipt:
    """What a provider returns after it accepts an instruction."""

    reference: str
    provider: str


class PaymentProvider:
    """Interface every provider must implement."""

    name = "base"

    def charge(self, *, project: Project, payer) -> PaymentReceipt:
        """Collect the project amount from the client into escrow."""
        raise NotImplementedError

    def payout(self, *, project: Project, transaction: Transaction) -> PaymentReceipt:
        """Pay released escrow to the freelancer."""
        raise NotImplementedError

    def refund(self, *, project: Project, transaction: Transaction) -> PaymentReceipt:
        """Return escrowed money to the client."""
        raise NotImplementedError


class SimulatedProvider(PaymentProvider):
    """Settles instantly. Used for development and the automated tests.

    The reference looks like a real provider reference so the ledger and the
    UI are exercised exactly as they will be with a live integration.
    """

    name = "simulated"

    def _receipt(self, action: str) -> PaymentReceipt:
        return PaymentReceipt(reference=f"SIM-{action}-{secrets.token_hex(6).upper()}", provider=self.name)

    def charge(self, *, project: Project, payer) -> PaymentReceipt:
        return self._receipt("DEP")

    def payout(self, *, project: Project, transaction: Transaction) -> PaymentReceipt:
        return self._receipt("PAY")

    def refund(self, *, project: Project, transaction: Transaction) -> PaymentReceipt:
        return self._receipt("RFD")


PROVIDERS = {SimulatedProvider.name: SimulatedProvider}

STATE_LABELS = {
    "unfunded": "Not funded",
    "held": "Held in escrow",
    "released": "Released to freelancer",
    "refunded": "Refunded to client",
}


def get_provider(name: str | None = None) -> PaymentProvider:
    provider_name = name or current_app.config.get("PAYMENT_PROVIDER", SimulatedProvider.name)
    provider_class = PROVIDERS.get(provider_name)
    if provider_class is None:
        raise PaymentError(f"Payment provider {provider_name!r} is not configured.")
    return provider_class()


def escrow_transaction(project: Project) -> Transaction | None:
    """The project's escrow record, if one was ever created."""
    return Transaction.query.filter_by(
        project_id=project.id, kind=Transaction.KIND_ESCROW
    ).order_by(Transaction.id.desc()).first()


def escrow_state(project: Project) -> str:
    transaction = escrow_transaction(project)
    return transaction.status if transaction is not None else "unfunded"


def fund_project(project: Project, client, provider: PaymentProvider | None = None) -> Transaction:
    """Hold the agreed amount in escrow. The caller owns the transaction and commit."""
    if project.status in ("completed", "cancelled"):
        raise PaymentError("This project is closed, so it cannot be funded.")
    existing = escrow_transaction(project)
    if existing is not None:
        if existing.status in ("held", "released"):
            raise PaymentError("This project is already funded.")
        raise PaymentError("This project's escrow was already settled. Open a new project instead.")
    provider = provider or get_provider()
    receipt = provider.charge(project=project, payer=client)
    transaction = Transaction(
        project_id=project.id,
        payer_id=project.client_id,
        payee_id=project.freelancer_id,
        amount=project.agreed_amount,
        currency=project.currency,
        kind=Transaction.KIND_ESCROW,
        provider=receipt.provider,
        provider_reference=receipt.reference,
        status="held",
    )
    db.session.add(transaction)
    notifications.notify(
        project.freelancer_id, "escrow_funded", currency=project.currency,
        amount=f"{project.agreed_amount:,.2f}", title=project.job.title,
        target_url=url_for("marketplace.project_detail", project_id=project.id),
    )
    return transaction


def release_escrow(project: Project, provider: PaymentProvider | None = None) -> Transaction:
    """Pay held escrow to the freelancer. The caller owns the transaction and commit."""
    transaction = escrow_transaction(project)
    if transaction is None or transaction.status != "held":
        raise PaymentError("This project has no escrow funds to release.")
    provider = provider or get_provider(transaction.provider)
    receipt = provider.payout(project=project, transaction=transaction)
    transaction.status = "released"
    transaction.settlement_reference = receipt.reference
    notifications.notify(
        project.freelancer_id, "payment_released", currency=project.currency,
        amount=f"{transaction.amount:,.2f}", title=project.job.title,
        target_url=url_for("dashboard.earnings"),
    )
    return transaction


def refund_escrow(project: Project, provider: PaymentProvider | None = None) -> Transaction:
    """Return held escrow to the client. The caller owns the transaction and commit."""
    transaction = escrow_transaction(project)
    if transaction is None or transaction.status != "held":
        raise PaymentError("Only funds still held in escrow can be refunded.")
    provider = provider or get_provider(transaction.provider)
    receipt = provider.refund(project=project, transaction=transaction)
    transaction.status = "refunded"
    transaction.settlement_reference = receipt.reference
    notifications.notify(
        project.client_id, "payment_refunded", currency=project.currency,
        amount=f"{transaction.amount:,.2f}", title=project.job.title,
        target_url=url_for("dashboard.earnings"),
    )
    return transaction
