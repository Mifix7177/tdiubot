// Telegram Bot Client Engine - Message Hub
const API_BASE = "";

let currentUser = {
  id: 7058416364,
  language: "ru",
  fullName: "Пользователь Telegram"
};

const chatStream = document.getElementById("chatStream");
const keyboardContainer = document.getElementById("keyboardContainer");
const msgInput = document.getElementById("msgInput");
const toggleKbdBtn = document.getElementById("toggleKbdBtn");
const attachBtn = document.getElementById("attachBtn");
const micBtn = document.getElementById("micBtn");
const hiddenFileInput = document.getElementById("hiddenFileInput");
const langSelect = document.getElementById("langSelect");

function getTimeString() {
  const now = new Date();
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

function scrollToBottom() {
  chatStream.scrollTop = chatStream.scrollHeight;
}

function appendUserBubble(text) {
  const group = document.createElement("div");
  group.className = "tg-bubble-group tg-bubble-out";
  
  const bubble = document.createElement("div");
  bubble.className = "tg-bubble";
  bubble.innerHTML = text.replace(/\n/g, "<br/>");
  
  const meta = document.createElement("span");
  meta.className = "tg-bubble-meta";
  meta.innerHTML = `${getTimeString()} <span class="tg-ticks">✓✓</span>`;
  
  bubble.appendChild(meta);
  group.appendChild(bubble);
  chatStream.appendChild(group);
  scrollToBottom();
}

function appendBotBubble(htmlContent) {
  const group = document.createElement("div");
  group.className = "tg-bubble-group tg-bubble-in";
  
  const bubble = document.createElement("div");
  bubble.className = "tg-bubble";
  bubble.innerHTML = htmlContent.replace(/\n/g, "<br/>");
  
  const meta = document.createElement("span");
  meta.className = "tg-bubble-meta";
  meta.innerHTML = getTimeString();
  
  bubble.appendChild(meta);
  group.appendChild(bubble);
  chatStream.appendChild(group);
  scrollToBottom();
}

function renderReplyKeyboard(keyboardGrid) {
  keyboardContainer.innerHTML = "";
  
  if (!keyboardGrid || keyboardGrid.length === 0) {
    return;
  }
  
  keyboardGrid.forEach(row => {
    const rowDiv = document.createElement("div");
    rowDiv.className = "tg-keyboard-row";
    
    row.forEach(btnData => {
      const btn = document.createElement("button");
      btn.className = "tg-kbd-btn";
      
      const text = btnData.display_text || btnData.text || "";
      btn.innerHTML = text;
      
      if (text.includes("Назад") || text.includes("Orqaga") || text.includes("Back")) {
        btn.classList.add("tg-btn-back");
      }
      
      btn.onclick = () => {
        btn.style.transform = "scale(0.96)";
        setTimeout(() => { btn.style.transform = ""; }, 100);
        sendUserAction(text, btnData.payload);
      };
      
      rowDiv.appendChild(btn);
    });
    
    keyboardContainer.appendChild(rowDiv);
  });
}

async function sendUserAction(actionText, customPayload = null) {
  appendUserBubble(actionText);
  
  try {
    const res = await fetch(`${API_BASE}/api/bot/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser.id,
        action_text: actionText,
        custom_payload: customPayload,
        language: currentUser.language
      })
    });
    
    const data = await res.json();
    const resp = data.response;
    
    setTimeout(() => {
      appendBotBubble(resp.text);
      renderReplyKeyboard(resp.keyboard);
    }, 200);
    
  } catch (err) {
    appendBotBubble("⚠️ Ошибка связи с ботом.");
    console.error(err);
  }
}

async function sendMediaMessage(mediaType, label) {
  appendUserBubble(`${label}`);
  try {
    const res = await fetch(`${API_BASE}/api/bot/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser.id,
        action_text: `[${mediaType.toUpperCase()}] ${label}`,
        language: currentUser.language
      })
    });
    const data = await res.json();
    const resp = data.response;
    setTimeout(() => {
      appendBotBubble(resp.text);
      renderReplyKeyboard(resp.keyboard);
    }, 200);
  } catch (err) {
    console.error(err);
  }
}

async function startConversation() {
  chatStream.innerHTML = "";
  try {
    const res = await fetch(`${API_BASE}/api/bot/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: currentUser.id })
    });
    const data = await res.json();
    
    // Simulate natural start flow matching screenshot
    appendUserBubble("/start");
    setTimeout(() => {
      appendBotBubble(data.response.text);
      renderReplyKeyboard(data.response.keyboard);
    }, 200);
  } catch (err) {
    console.error(err);
  }
}

function init() {
  // Enter key sends text
  if (msgInput) {
    msgInput.onkeydown = (e) => {
      if (e.key === "Enter") {
        const val = msgInput.value.trim();
        if (!val) return;
        msgInput.value = "";
        sendUserAction(val);
      }
    };
  }

  // Toggle Keyboard visibility
  if (toggleKbdBtn) {
    toggleKbdBtn.onclick = () => {
      if (keyboardContainer.style.display === "none") {
        keyboardContainer.style.display = "flex";
      } else {
        keyboardContainer.style.display = "none";
      }
    };
  }

  // Attachment button
  if (attachBtn && hiddenFileInput) {
    attachBtn.onclick = () => hiddenFileInput.click();
    hiddenFileInput.onchange = (e) => {
      if (e.target.files.length > 0) {
        const file = e.target.files[0];
        const isVideo = file.type.startsWith("video");
        const icon = isVideo ? "🎥" : "📷";
        sendMediaMessage(isVideo ? "video" : "photo", `${icon} ${file.name}`);
      }
    };
  }

  // Mic button (Green circle) -> voice message simulation
  if (micBtn) {
    micBtn.onclick = () => {
      sendMediaMessage("voice", "🎙 Голосовое сообщение (0:08)");
    };
  }

  // Language selector
  if (langSelect) {
    langSelect.onchange = async (e) => {
      currentUser.language = e.target.value;
      await fetch(`${API_BASE}/api/bot/switch-lang`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: currentUser.id, language: currentUser.language })
      });
      startConversation();
    };
  }

  startConversation();
}

window.addEventListener("DOMContentLoaded", init);
