/* Live-ish project conversation.
 *
 * Polls /projects/<id>/messages.json for messages newer than the newest one
 * already on the page, appends them, and tells the server the conversation has
 * been read so the navigation badge stays accurate without a reload.
 *
 * Polling is used instead of WebSockets on purpose: it needs no extra service,
 * works on any host that runs Flask, and is easy to explain and test. The
 * interval is deliberately modest (5s) to keep the server load predictable.
 */
(() => {
  const thread = document.getElementById('message-thread');
  if (!thread) return;

  const projectId = thread.dataset.projectId;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const badge = document.getElementById('navUnreadMessages');
  const POLL_MS = 5000;
  let latestId = Number(thread.dataset.latestId || 0);
  let polling = false;

  function setBadge(count) {
    if (!badge) return;
    badge.textContent = String(count);
    badge.classList.toggle('d-none', count === 0);
    badge.setAttribute('aria-label', `${count} unread messages`);
  }

  function appendMessage(message) {
    const bubble = document.createElement('div');
    bubble.className = `message${message.mine ? ' message-mine' : ''}`;
    bubble.dataset.messageId = String(message.id);

    const sender = document.createElement('strong');
    sender.textContent = message.sender;
    const body = document.createElement('p');
    body.textContent = message.body;
    const meta = document.createElement('small');
    meta.textContent = message.created_at;

    bubble.append(sender, body, meta);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  async function markRead() {
    try {
      await fetch(`/projects/${projectId}/messages/read`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken },
      });
    } catch (error) {
      console.warn('Could not mark messages as read', error);
    }
  }

  async function poll() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const response = await fetch(
        `/projects/${projectId}/messages.json?since_id=${latestId}`,
        { credentials: 'same-origin', headers: { Accept: 'application/json' } },
      );
      if (!response.ok) return;
      const payload = await response.json();
      if (payload.messages.length) {
        payload.messages.forEach(appendMessage);
        latestId = payload.latest_id;
        if (payload.messages.some((message) => !message.mine)) await markRead();
      }
      setBadge(payload.unread_total);
    } catch (error) {
      /* A transient network failure should not break the page; try again next tick. */
      console.warn('Message polling failed', error);
    } finally {
      polling = false;
    }
  }

  setInterval(poll, POLL_MS);
})();
