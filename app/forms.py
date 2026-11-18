"""Validated web forms."""
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (BooleanField, DecimalField, PasswordField, SelectField, StringField,
                     SelectChoice, SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, EqualTo, Length, NumberRange,
                                Optional, ValidationError)


class RegisterForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    role = SelectField("I want to", choices=[SelectChoice("freelancer", "Find freelance work"), SelectChoice("client", "Hire student talent")], validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=10, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Create account")

    def validate_password(self, field):
        value = field.data or ""
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValidationError("Use at least one letter and one number.")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class ProfileForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=120)])
    location = StringField("Location", validators=[Optional(), Length(max=120)])
    headline = StringField("Professional headline", validators=[Optional(), Length(max=160)])
    bio = TextAreaField("About", validators=[Optional(), Length(max=3000)])
    university = StringField("University or college", validators=[Optional(), Length(max=160)])
    hourly_rate = DecimalField("Hourly rate (KES)", validators=[Optional(), NumberRange(min=0, max=Decimal("99999999.99"))], places=2)
    skills = StringField("Skills (comma separated)", validators=[Optional(), Length(max=1000)])
    profile_image = FileField("Profile photo", validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp"], "Use a PNG, JPG or WEBP image.")])
    organization_name = StringField("Organization name", validators=[Optional(), Length(max=160)])
    website = StringField("Website", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save profile")

    def validate_website(self, field):
        if field.data and not valid_http_url(field.data):
            raise ValidationError("Enter a link beginning with https:// or http://.")

    def validate_profile_image(self, field):
        validate_image_signature(field)


class JobForm(FlaskForm):
    title = StringField("Job title", validators=[DataRequired(), Length(min=5, max=180)])
    category = StringField("Category", validators=[DataRequired(), Length(max=80)])
    description = TextAreaField("What needs to be done?", validators=[DataRequired(), Length(min=30, max=10000)])
    budget_min = DecimalField("Minimum budget (KES)", validators=[Optional(), NumberRange(min=0, max=Decimal("9999999999.99"))], places=2)
    budget_max = DecimalField("Maximum budget (KES)", validators=[Optional(), NumberRange(min=0, max=Decimal("9999999999.99"))], places=2)
    location = StringField("Location", validators=[Optional(), Length(max=120)])
    submit = SubmitField("Publish job")

    def validate_budget_max(self, field):
        if field.data is not None and self.budget_min.data is not None and field.data < self.budget_min.data:
            raise ValidationError("Maximum budget must be at least the minimum budget.")


class ApplicationForm(FlaskForm):
    cover_letter = TextAreaField("Why are you a good fit?", validators=[DataRequired(), Length(min=30, max=5000)])
    proposed_rate = DecimalField("Your proposed amount (KES)", validators=[Optional(), NumberRange(min=Decimal("0.01"), max=Decimal("9999999999.99"))], places=2)
    submit = SubmitField("Send application")


class MessageForm(FlaskForm):
    body = TextAreaField("Message", validators=[DataRequired(), Length(min=1, max=5000)])
    submit = SubmitField("Send")


class ReviewForm(FlaskForm):
    rating = SelectField("Rating", choices=[SelectChoice(str(n), f"{n} stars") for n in range(5, 0, -1)], coerce=int)
    comment = TextAreaField("Review", validators=[Optional(), Length(max=3000)])
    submit = SubmitField("Submit review")


class PortfolioForm(FlaskForm):
    title = StringField("Project title", validators=[DataRequired(), Length(min=2, max=160)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=3000)])
    project_url = StringField("Project link", validators=[Optional(), Length(max=500)])
    image = FileField("Project image", validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp"], "Use a PNG, JPG or WEBP image.")])
    submit = SubmitField("Add project")

    def validate_project_url(self, field):
        if field.data and not valid_http_url(field.data):
            raise ValidationError("Enter a link beginning with https:// or http://.")

    def validate_image(self, field):
        validate_image_signature(field)


class ReportForm(FlaskForm):
    reason = SelectField("Reason", choices=[SelectChoice("fraud", "Fraud or scam"), SelectChoice("harassment", "Harassment"), SelectChoice("spam", "Spam"), SelectChoice("misconduct", "Unprofessional conduct"), SelectChoice("other", "Other")], validators=[DataRequired()])
    details = TextAreaField("Details", validators=[Optional(), Length(max=3000)])
    submit = SubmitField("Submit report")


def valid_http_url(value):
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate_image_signature(field):
    upload = field.data
    if not upload or not upload.filename:
        return
    extension = Path(upload.filename).suffix.lower()
    header = upload.stream.read(12)
    upload.stream.seek(0)
    valid = (
        extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
        or extension in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff")
        or extension == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    )
    if not valid:
        raise ValidationError("The file contents do not match a supported image format.")
