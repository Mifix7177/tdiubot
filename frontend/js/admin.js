// Admin Portal - Dedicated Messages & Feedback Management Engine
const API_BASE = "";

let currentFilter = "all";
let allMessages = [];
let activeReplyMessageId = null;

async function loadMessages() {
  try {
    const res = await fetch(`${API_BASE}/api/admin/messages`);
    allMessages = await res.json();

    updateMetrics(allMessages);
    renderTable();
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
}

function updateMetrics(messages) {
  const total = messages.length;
  const newCount = messages.filter(m => m.status === "new").length;
  const confessionsCount = messages.filter(m => m.msg_type === "confession").length;
  const repliedCount = messages.filter(m => m.status === "replied").length;
  const helpCount = messages.filter(m => m.msg_type === "help").length;

  document.getElementById("statTotal").innerText = total;
  document.getElementById("statNew").innerText = newCount;
  document.getElementById("statConfessions").innerText = confessionsCount;
  document.getElementById("statReplied").innerText = repliedCount;

  // Update sidebar badges
  const badgeAll = document.getElementById("badgeAll");
  if (badgeAll) badgeAll.innerText = total;

  const badgeHelp = document.getElementById("badgeHelp");
  if (badgeHelp) badgeHelp.innerText = helpCount;

  const badgeConfession = document.getElementById("badgeConfession");
  if (badgeConfession) badgeConfession.innerText = confessionsCount;
}

function setFilter(filterType, btnEl = null) {
  currentFilter = filterType;

  // Update active pill button
  document.querySelectorAll(".filter-pill").forEach(el => el.classList.remove("active"));
  if (btnEl) {
    btnEl.classList.add("active");
  }

  // Update sidebar nav active state
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  const navItem = document.getElementById(`nav-${filterType}`);
  if (navItem) navItem.classList.add("active");

  renderTable();
}

function renderTable() {
  const tbody = document.getElementById("messagesTableBody");
  tbody.innerHTML = "";

  let filtered = allMessages;
  if (currentFilter === "help") {
    filtered = allMessages.filter(m => m.msg_type === "help");
  } else if (currentFilter === "confession") {
    filtered = allMessages.filter(m => m.msg_type === "confession");
  } else if (currentFilter === "normal") {
    filtered = allMessages.filter(m => m.msg_type === "normal");
  } else if (currentFilter === "media") {
    filtered = allMessages.filter(m => ["photo", "video", "voice", "video_note"].includes(m.msg_type) || m.media_type);
  }

  const countEl = document.getElementById("filteredCount");
  if (countEl) countEl.innerText = `(Ko'rsatildi: ${filtered.length} ta)`;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:36px; color:var(--text-muted); font-size:14px;">Hozircha ushbu bo'limda xabarlar yo'q.</td></tr>`;
    return;
  }

  filtered.forEach(m => {
    const tr = document.createElement("tr");

    // Type badge format
    let typeBadge = "";
    if (m.msg_type === "help") {
      typeBadge = '<span class="badge badge-new" style="background:rgba(239,68,68,0.15); color:#f87171;">🆘 Yordam</span>';
    } else if (m.msg_type === "confession") {
      typeBadge = '<span class="badge" style="background:rgba(236,72,153,0.15); color:#f472b6;">💌 Iqrornoma</span>';
    } else if (m.msg_type === "photo") {
      typeBadge = '<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa;">📷 Foto</span>';
    } else if (m.msg_type === "video") {
      typeBadge = '<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc;">🎥 Video</span>';
    } else if (m.msg_type === "voice") {
      typeBadge = '<span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80;">🎙 Ovozli</span>';
    } else if (m.msg_type === "video_note") {
      typeBadge = '<span class="badge" style="background:rgba(234,179,8,0.15); color:#facc15;">🎞 Video-xabar</span>';
    } else {
      typeBadge = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#cbd5e1;">✉️ Oddiy</span>';
    }

    let statusBadge = "";
    if (m.status === "new") {
      statusBadge = '<span class="badge" style="background:rgba(234,179,8,0.15); color:#facc15;">⏳ Yangi (Kutilmoqda)</span>';
    } else if (m.status === "published") {
      statusBadge = `<span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80;">Отправил</span>`;
    } else if (m.status === "rejected") {
      statusBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171;">❌ Rad etilgan</span>';
    } else if (m.status === "cancelled") {
      statusBadge = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#cbd5e1;">🚫 Bekor qilingan</span>';
    } else {
      statusBadge = '<span class="badge badge-replied">Javob berilgan ✅</span>';
    }

    // Content preview with reply if already replied
    let contentHtml = `<div>${escapeHtml(m.content)}</div>`;
    if (m.admin_reply) {
      contentHtml += `<div style="font-size:12px; color:#34d399; margin-top:4px; border-left:2px solid #10b981; padding-left:8px;"><b>Javobingiz:</b> <i>${escapeHtml(m.admin_reply)}</i></div>`;
    }

    let approveBtn = "";
    if (m.status === "new") {
      approveBtn = `
        <button class="btn btn-sm" style="background:#10b981; color:white; margin-right:4px;" onclick="publishMessage(${m.id})" title="Kanalga chiqarish">
          ✅ Chiqarish (Approve)
        </button>
        <button class="btn btn-sm" style="background:#f59e0b; color:white; margin-right:4px;" onclick="rejectMessage(${m.id})" title="Rad etish">
          ❌ Rad etish
        </button>
      `;
    }

    const idHtml = m.channel_order 
      ? `<b style="color:#10b981;">#${m.channel_order}</b> <small style="color:var(--text-muted); font-size:11px;">(ID: ${m.id})</small>`
      : `<b>#${m.id}</b>`;

    tr.innerHTML = `
      <td>${idHtml}</td>
      <td>
        <b>${escapeHtml(m.user_name || "Noma'lum")}</b><br>
        <small style="color:var(--text-muted); font-family:monospace;">ID: ${m.user_id}</small>
      </td>
      <td>${typeBadge}</td>
      <td style="max-width:340px; word-break:break-word;">${contentHtml}</td>
      <td>${statusBadge}</td>
      <td><small style="color:var(--text-muted);">${m.created_at || ""}</small></td>
      <td style="white-space:nowrap;">
        ${approveBtn}
        <button class="btn btn-primary btn-sm" onclick="openReplyModal(${m.id}, '${escapeHtml(m.user_name || '')}', '${escapeHtml(m.content)}')">
          💬 Javob yozish
        </button>
        <button class="btn btn-danger btn-sm" style="margin-left:6px;" onclick="deleteMessage(${m.id})">
          🗑
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function openReplyModal(msgId, userName, content) {
  activeReplyMessageId = msgId;
  document.getElementById("replyTargetUser").innerText = userName;
  document.getElementById("replyOrigContent").innerText = content;
  document.getElementById("replyTextArea").value = "";
  document.getElementById("replyModal").classList.add("active");
  setTimeout(() => document.getElementById("replyTextArea").focus(), 150);
}

function closeReplyModal() {
  document.getElementById("replyModal").classList.remove("active");
}

async function sendReply() {
  const text = document.getElementById("replyTextArea").value.trim();
  if (!text) {
    alert("Iltimos, javob matnini yozing!");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/admin/messages/${activeReplyMessageId}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reply_text: text })
    });

    const data = await res.json();
    if (data.status === "ok") {
      alert("✅ Javob talabaga Telegram orqali muvaffaqiyatli yuborildi!");
      closeReplyModal();
      loadMessages();
    }
  } catch (err) {
    alert("Javob yuborishda xatolik: " + err.message);
  }
}

async function deleteMessage(msgId) {
  if (!confirm("Haqiqatan ham ushbu xabarni o'chirmoqchimisiz?")) return;
  try {
    await fetch(`${API_BASE}/api/admin/messages/${msgId}`, { method: "DELETE" });
    loadMessages();
  } catch (err) {
    alert("O'chirishda xatolik yuz berdi.");
  }
}

async function publishMessage(id) {
  if (!confirm(`Haqiqatan ham #${id}-xabarni kanalga chiqarmoqchimisiz (Approve)?`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/admin/messages/${id}/publish`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      alert(`✅ Xabar muvaffaqiyatli kanalga chiqarildi! (Kanal xabar ID: ${data.channel_message_id})`);
      loadMessages();
    } else {
      alert(`Xatolik: ${data.detail || "Chiqarib bo'lmadi"}`);
    }
  } catch (err) {
    alert(`Server xatosi: ${err}`);
  }
}

async function rejectMessage(id) {
  if (!confirm(`Haqiqatan ham #${id}-xabarni rad etmoqchimisiz?`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/admin/messages/${id}/reject`, { method: "POST" });
    if (res.ok) {
      alert(`❌ Xabar rad etildi.`);
      loadMessages();
    } else {
      alert("Rad etib bo'lmadi.");
    }
  } catch (err) {
    alert(`Server xatosi: ${err}`);
  }
}

function openBroadcastModal() {
  document.getElementById("broadcastText").value = "";
  document.getElementById("broadcastModal").classList.add("active");
}

function closeBroadcastModal() {
  document.getElementById("broadcastModal").classList.remove("active");
}

async function sendBroadcast() {
  const text = document.getElementById("broadcastText").value.trim();
  if (!text) {
    alert("Iltimos, e'lon matnini yozing!");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/admin/broadcast`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    alert(`✅ Xabar yuborildi! Qamrab olingan foydalanuvchilar: ${data.sent_count}`);
    closeBroadcastModal();
    loadMessages();
  } catch (err) {
    alert("Xatolik: " + err.message);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  loadMessages();
  // Auto-refresh inbox every 10 seconds
  setInterval(loadMessages, 10000);
});
