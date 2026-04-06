let notificationPermissionRequested = false;
function requestNotificationPermission() {
    if (!notificationPermissionRequested && 'Notification' in window && Notification.permission === 'default') {
        notificationPermissionRequested = true;
        Notification.requestPermission();
    }
}

function triggerNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        new Notification(title, { body: body, icon: '/static/icons/alert.png' });
    }
}

// Global state
let lastVoiceToken = 0;
let speaking = false;
let currentSpeedLevel = 0; // 0=Safe, 1=Warning, 2=Danger
let lastSpeedVal = 0;
let currentSpeed = 0;
let lastSpeedWarningTime = 0;
let speedMode = "gps";
let watchId = null;
let lastEventId = 0;
let activeChatOrgId = null;
let lastChatLength = 0;

// Cloud Streaming State
let webcamStream = null;
let isStreaming = false;
let streamingInterval = null;
const streamFPS = 8; 
let isProcessingFrame = false;
let lastBoxes = []; // Store current detection boxes
let lastStatus = {}; // Store last full status

// Theme logic
function applyTheme(theme) {
  const body = document.body;
  const themeIcon = document.getElementById("themeIcon");
  body.classList.remove("theme-dark", "theme-light");
  body.classList.add(theme);
  if (themeIcon) {
    themeIcon.textContent = theme === "theme-light" ? "☀️" : "🌙";
  }
}

function initTheme() {
  const themeToggle = document.getElementById("themeToggle");
  if (themeToggle) {
    const stored = localStorage.getItem("theme");
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const initial = stored || (prefersDark ? "theme-dark" : "theme-light");
    applyTheme(initial);

    themeToggle.onclick = () => {
      const next = document.body.classList.contains("theme-dark") ? "theme-light" : "theme-dark";
      applyTheme(next);
      localStorage.setItem("theme", next);
    };
  } else {
    const stored = localStorage.getItem("theme") || "theme-dark";
    applyTheme(stored);
  }
}

function badgeForLevel(level) {
  if (level <= 0) return { cls: "bg-success", text: "SAFE" };
  if (level === 1) return { cls: "bg-warning text-dark", text: "WARNING" };
  if (level === 2) return { cls: "bg-danger", text: "DANGER" };
  return { cls: "bg-danger", text: "CRITICAL" };
}

function setDot(dotEl, level) {
  if (!dotEl) return;
  dotEl.classList.remove("dot-warn", "dot-danger");
  if (level === 1) dotEl.classList.add("dot-warn");
  if (level >= 2) dotEl.classList.add("dot-danger");
}

function speak(text) {
  const voiceToggle = document.getElementById("voiceToggle");
  const voiceEnabled = voiceToggle ? voiceToggle.checked : true;
  if (!voiceEnabled) return;
  if (!("speechSynthesis" in window)) return;
  if (!text) return;

  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    u.pitch = 1.0;
    u.volume = 1.0;
    speaking = true;
    u.onend = () => (speaking = false);
    u.onerror = () => (speaking = false);
    window.speechSynthesis.speak(u);
  } catch (_) {
    // ignore
  }
}

async function postJSON(url) {
  const res = await fetch(url, { method: "POST" });
  return await res.json();
}

async function pollStatus() {
  const applicationList = document.getElementById("applicationList");
  if (applicationList) fetchApplications();

  const overallText = document.getElementById("overallText");
  if (!overallText) return;

  const overlayIdle = document.getElementById("overlayIdle");
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) return;
    const s = await res.json();

    const fpsCapture = document.getElementById("fpsCapture");
    const fpsProcess = document.getElementById("fpsProcess");
    if (fpsCapture) fpsCapture.textContent = (s.fps_capture ?? 0).toFixed(1);
    if (fpsProcess) fpsProcess.textContent = (s.fps_process ?? 0).toFixed(1);

    let apiOverallLevel = s.overall_level;
    let fallbackMessage = s.overall_message;

    let finalOverallLevel = s.camera_running ? Math.max(apiOverallLevel, currentSpeedLevel) : 0;
    if (s.camera_running) {
        if (currentSpeedLevel >= 2 && finalOverallLevel >= 2) {
            fallbackMessage = "CRITICAL: SPEED";
        } else if (currentSpeedLevel >= 1 && finalOverallLevel >= 1 && apiOverallLevel < 2) {
            fallbackMessage = "WARNING: SPEED";
        }
    }

    overallText.textContent = s.camera_running ? fallbackMessage : "Stopped";
    const overallBadge = document.getElementById("overallBadge");
    if (overallBadge) {
      const ob = badgeForLevel(finalOverallLevel);
      overallBadge.className = `badge rounded-pill px-3 py-2 ${ob.cls}`;
      overallBadge.textContent = s.camera_running ? ob.text : "STOPPED";
    }

    const dot = document.getElementById("dotOverall");
    setDot(dot, finalOverallLevel);

    const eyesTimer = document.getElementById("eyesTimer");
    const phoneTimer = document.getElementById("phoneTimer");
    if (eyesTimer) eyesTimer.textContent = (s.drowsiness_duration_s ?? 0).toFixed(1);
    if (phoneTimer) phoneTimer.textContent = (s.phone_duration_s ?? 0).toFixed(1);

    const drowsyText = document.getElementById("drowsyText");
    const dBadge = document.getElementById("drowsyBadge");
    if (drowsyText && dBadge) {
      drowsyText.textContent = s.drowsiness_message ?? "-";
      const db = badgeForLevel(s.drowsiness_level ?? 0);
      dBadge.className = `badge rounded-pill ${db.cls}`;
      dBadge.textContent = db.text;
    }

    const phoneText = document.getElementById("phoneText");
    const pBadge = document.getElementById("phoneBadge");
    if (phoneText && pBadge) {
      phoneText.textContent = s.phone_message ?? "-";
      const pb = badgeForLevel(s.phone_level ?? 0);
      pBadge.className = `badge rounded-pill ${pb.cls}`;
      pBadge.textContent = pb.text;
    }

    const earVal = document.getElementById("earVal");
    const phoneConfVal = document.getElementById("phoneConfVal");
    if (earVal)
      earVal.textContent =
        s.ear === null || s.ear === undefined ? "-" : Number(s.ear).toFixed(3);
    if (phoneConfVal) phoneConfVal.textContent = Number(s.phone_conf ?? 0).toFixed(2);

    const critOverlay = document.getElementById("overlayCritical");
    if (critOverlay) {
      if ((s.phone_level ?? 0) >= 3) critOverlay.classList.remove("d-none");
      else critOverlay.classList.add("d-none");
    }

    if (overlayIdle) {
      if (s.camera_running) overlayIdle.classList.add("d-none");
      else overlayIdle.classList.remove("d-none");
    }

    if ((s.voice_token ?? 0) > lastVoiceToken) {
      lastVoiceToken = s.voice_token;
      speak(s.voice_text ?? "");
    }
  } catch (_) {
    // ignore
  }
}

function updateSpeedometer() {
  const speedTextEl = document.getElementById("speedometerText");
  const speedBarEl = document.getElementById("speedometerBar");
  const speedBadgeEl = document.getElementById("speedStatusBadge");
  if (!speedTextEl || !speedBarEl || !speedBadgeEl) return;

  if (speedMode === "simulated") {
    let change = (Math.random() - 0.45) * 15;
    if (Math.random() < 0.05) change += 20;
    if (Math.random() < 0.05) change -= 20;
    currentSpeed += change;
    if (currentSpeed < 0) currentSpeed = 0;
    if (currentSpeed > 120) currentSpeed = 120;
  }

  lastSpeedVal += (currentSpeed - lastSpeedVal) * 0.5;
  let speedInt = Math.round(lastSpeedVal);

  speedTextEl.textContent = speedInt + " km/h";
  speedBarEl.style.width = Math.min((speedInt / 120) * 100, 100) + "%";
  speedBarEl.setAttribute("aria-valuenow", speedInt);

  speedTextEl.className = "display-4 fw-bold mb-3 ";
  speedBarEl.className = "progress-bar progress-bar-animated progress-bar-striped ";
  speedBadgeEl.className = "badge border border-secondary ";

  if (speedInt <= 70) {
    speedTextEl.classList.add("text-success");
    speedBarEl.classList.add("bg-success");
    speedBadgeEl.classList.add("bg-success-subtle", "text-success", "border-success");
    speedBadgeEl.textContent = "SAFE";
    currentSpeedLevel = 0;
  } else if (speedInt <= 100) {
    speedTextEl.classList.add("text-warning");
    speedBarEl.classList.add("bg-warning");
    speedBadgeEl.classList.add("bg-warning-subtle", "text-warning", "border-warning");
    speedBadgeEl.textContent = "GO SLOW";
    currentSpeedLevel = 1;
  } else {
    speedTextEl.classList.add("text-danger");
    speedBarEl.classList.add("bg-danger");
    speedBadgeEl.classList.add("bg-danger-subtle", "text-danger", "border-danger");
    speedBadgeEl.textContent = "OVER SPEED";
    currentSpeedLevel = 2;

    let now = Date.now();
    if (now - lastSpeedWarningTime > 5000 && !speaking) {
      speak("Over Speed! Slow down");
      lastSpeedWarningTime = now;
    }
  }
}

function escapeHTML(str) {
    const p = document.createElement('p');
    p.textContent = str;
    return p.innerHTML;
}

function getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    if (ext === 'pdf') return 'fa-file-pdf text-danger';
    if (['doc', 'docx'].includes(ext)) return 'fa-file-word text-primary';
    if (['xls', 'xlsx'].includes(ext)) return 'fa-file-excel text-success';
    if (['ppt', 'pptx'].includes(ext)) return 'fa-file-powerpoint text-warning';
    if (['zip', 'rar', '7z'].includes(ext)) return 'fa-file-archive text-warning';
    return 'fa-file-alt text-secondary';
}

function linkify(text) {
    const escaped = escapeHTML(text);
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    return escaped.replace(urlRegex, (url) => {
        return `<a href="${url}" target="_blank" style="color: #60a5fa !important; text-decoration: underline !important; font-weight: bold;">${url}</a>`;
    });
}

async function fetchDriverChat() {
    const driverChatBox = document.getElementById("driverChatBox");
    if (!driverChatBox || !activeChatOrgId) return;
    try {
        const res = await fetch(`/api/chat/messages?org_id=${activeChatOrgId}`);
        const msgs = await res.json();
        if (msgs.length === lastChatLength) return;
        lastChatLength = msgs.length;
        
        driverChatBox.innerHTML = '<div class="text-center my-2"><span class="badge bg-dark border border-secondary text-warning" style="font-size: 0.7rem;"><i class="fas fa-lock me-1"></i> Messages are encrypted.</span></div>';
        
        let lastDateStr = null;
        const today = new Date().toDateString();
        const yesterday = new Date(Date.now() - 86400000).toDateString();

        msgs.forEach(m => {
            const d = new Date(m.timestamp);
            const dateStr = d.toDateString();
            if (dateStr !== lastDateStr) {
                let displayDate = (dateStr === today) ? 'Today' : (dateStr === yesterday ? 'Yesterday' : dateStr);
                const dateDiv = document.createElement('div');
                dateDiv.className = 'date-divider';
                dateDiv.innerHTML = `<span>${displayDate}</span>`;
                driverChatBox.appendChild(dateDiv);
                lastDateStr = dateStr;
            }

            const div = document.createElement('div');
            const isMe = m.sender_type === 'driver';
            div.className = `chat-row ${isMe ? 'me' : 'not-me'}`;
            const time = d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            let content = linkify(m.message);
            if (m.message_type === 'image') content = `<img src="${m.message}" alt="Image" class="img-fluid rounded" onclick="window.open(this.src)" style="cursor: pointer;">`;
            else if (m.message_type === 'voice') content = `<audio src="${m.message}" controls style="max-width: 100%;"></audio>`;
            else if (m.message_type === 'video') content = `<video src="${m.message}" controls preload="metadata" style="max-width: 100%; border-radius: 8px;"></video>`;
            else if (m.message_type === 'document') {
                const filename = m.message.split('/').pop().substring(33);
                content = `<div class="d-flex align-items-center p-2 bg-black-50 rounded border border-secondary shadow-sm">
                             <i class="fas ${getFileIcon(filename)} fs-4 me-3"></i>
                             <div class="flex-grow-1 overflow-hidden">
                               <div class="small fw-bold text-truncate" title="${filename}">${filename}</div>
                               <a href="${m.message}" target="_blank" class="text-primary small text-decoration-none">Download <i class="fas fa-download ms-1"></i></a>
                             </div>
                           </div>`;
            }
            
            const ticks = m.is_read ? '<i class="fas fa-check-double text-info"></i>' : '<i class="fas fa-check text-white"></i>';
            div.innerHTML = `
                <div class="chat-bubble ${isMe ? 'sender' : 'receiver'}">
                    <div class="chat-label fw-bold mb-1">${isMe ? 'You' : 'Organisation'}</div>
                    <div>${content}</div>
                    <div class="text-end mt-1" style="font-size: 0.65rem; opacity: 0.7;">
                        ${time}
                        ${isMe ? `<span class="ms-1">${ticks}</span>` : ''}
                    </div>
                </div>
            `;
            driverChatBox.appendChild(div);
        });
        driverChatBox.scrollTop = driverChatBox.scrollHeight;
    } catch(e) {}
}

async function fetchApplications() {
    const list = document.getElementById("applicationList");
    if (!list) return;
    try {
        const res = await fetch('/api/driver/applications');
        let apps = await res.json();
        
        // Only show pending applications in the main list
        apps = apps.filter(a => a.status === 'pending');
        
        const appCount = document.getElementById("appCount");
        if (appCount) appCount.innerText = apps.length;

        if (apps.length === 0) {
            list.innerHTML = '<li class="list-group-item bg-dark text-secondary border-secondary small text-center py-3">No active or pending applications</li>';
            return;
        }

        list.innerHTML = "";
        apps.forEach((a) => {
            const li = document.createElement('li');
            li.className = `list-group-item bg-dark text-light border-secondary d-flex justify-content-between align-items-center ${activeChatOrgId === a.organisation_id ? 'bg-secondary' : ''}`;
            const statusColor = a.status === 'accepted' ? 'text-success' : (a.status === 'pending' ? 'text-warning' : 'text-danger');
            
            li.innerHTML = `
                <div>
                  <div class="fw-bold">${a.org_name}</div>
                  <div class="small ${statusColor} fw-bold text-uppercase" style="font-size: 0.6rem;">${a.status}</div>
                </div>
                <button class="btn btn-sm btn-outline-info chat-btn" onclick="selectChatOrg(${a.organisation_id}, '${a.org_name.replace(/'/g, "\\'")}')">Chat</button>
            `;
            list.appendChild(li);
        });
    } catch (e) {
        console.error("Fetch apps error:", e);
    }
}

let lastNotifCount = -1;
async function fetchNotifications() {
    try {
        const res = await fetch('/api/notifications');
        const notifs = await res.json();
        const unreadCount = notifs.filter(n => !n.is_read).length;
        
        const badge = document.getElementById('notifBadge');
        if (badge) {
            if (unreadCount > 0) {
                badge.textContent = unreadCount;
                badge.classList.remove('d-none');
                if (unreadCount !== lastNotifCount && lastNotifCount !== -1) {
                    // Small pulse effect on new unread
                    badge.classList.add('pulse-notif');
                    setTimeout(() => badge.classList.remove('pulse-notif'), 2000);
                }
            } else {
                badge.classList.add('d-none');
            }
        }
        lastNotifCount = unreadCount;
        renderNotifications(notifs);
    } catch (e) {}
}

function renderNotifications(notifs) {
    const container = document.getElementById('notifItems');
    if (!container) return;
    if (notifs.length === 0) {
        container.innerHTML = '<div class="p-3 text-center text-muted small">No notifications</div>';
        return;
    }
    
    container.innerHTML = '';
    notifs.forEach(n => {
        const div = document.createElement('div');
        div.className = `p-2 notif-item ${n.is_read ? '' : 'unread'}`;
        const timeStr = new Date(n.timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        div.innerHTML = `
            <div class="d-flex justify-content-between">
                <span class="notif-title text-${n.type === 'success' ? 'success' : (n.type === 'warning' ? 'warning' : 'light')}">${n.title}</span>
                <span class="notif-time">${timeStr}</span>
            </div>
            <div class="notif-msg mt-1">${n.message}</div>
        `;
        container.appendChild(div);
    });
}

async function clearNotifs() {
    try {
        await fetch('/api/notifications/clear', {method:'POST'});
        fetchNotifications();
    } catch(e) {}
}

function selectChatOrg(id, name) {
    activeChatOrgId = id;
    const card = document.getElementById("driverChatCard");
    const title = document.getElementById("chatOrgTitle");
    if (card) card.style.display = 'block';
    if (title) title.innerText = `Chat with ${name}`;
    lastChatLength = 0;
    fetchDriverChat();
    fetchApplications();
}

function closeDriverChat() {
    activeChatOrgId = null;
    const card = document.getElementById("driverChatCard");
    if (card) card.style.display = 'none';
    fetchApplications();
}

// consolidated send function below


async function fetchDriverEvents() {
    const eventLogBody = document.getElementById("eventLogBody");
    if (!eventLogBody) return;
    try {
        const res = await fetch('/api/driver/events?limit=10');
        if (!res.ok) return;
        const events = await res.json();
        
        if (events.length === 0) {
            eventLogBody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-secondary">No recent events detected</td></tr>';
            return;
        }

        // Only refresh if IDs changed
        const currentIds = events.map(e => e.id).join(',');
        if (eventLogBody.dataset.lastIds === currentIds) return;
        eventLogBody.dataset.lastIds = currentIds;

        eventLogBody.innerHTML = '';
        events.forEach((e, idx) => {
            const tr = document.createElement('tr');
            const sevBadge = badgeForLevel(e.severity === 'SAFE' ? 0 : e.severity === 'WARNING' ? 1 : 2);
            
            tr.innerHTML = `
                <td class="ps-3 text-secondary font-monospace">${events.length - idx}</td>
                <td class="text-secondary small">${e.ts.includes('T') ? e.ts.split('T')[1] : e.ts}</td>
                <td><span class="badge border border-secondary bg-secondary-subtle text-info">${e.event_type}</span></td>
                <td><span class="badge ${sevBadge.cls}">${sevBadge.text}</span></td>
                <td class="text-end fw-bold">${Number(e.duration_s).toFixed(1)}</td>
                <td class="pe-3">${e.message}</td>
            `;
            eventLogBody.appendChild(tr);
        });
    } catch (e) {}
}

async function sendDriverChatMessage(msg, type = 'text') {
    if(!activeChatOrgId) return alert("Select a chat first.");
    try {
        const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({
                message: msg, 
                message_type: type,
                receiver_type: 'org',
                receiver_id: activeChatOrgId
            })
        });
        if (!res.ok) throw new Error("Failed to send message");
        fetchDriverChat();
    } catch(e) {
        console.error("Chat send error:", e);
        alert("Failed to send message. Please try again.");
    }
}

async function uploadChatFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch('/api/chat/upload', {
            method: 'POST',
            body: formData
        });
        if (!res.ok) throw new Error("Upload failed");
        return await res.json();
    } catch(e) {
        console.error("Upload error:", e);
        alert("Failed to upload file. Please check your connection.");
        return {ok: false};
    }
}

async function startBrowserCamera() {
    const webcam = document.getElementById('webcam');
    const videoCanvas = document.getElementById('videoCanvas');
    const captureCanvas = document.getElementById('captureCanvas');
    if (!webcam || !videoCanvas || !captureCanvas) return;

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ 
            video: { width: 640, height: 480, frameRate: 15 } 
        });
        webcam.srcObject = stream;
        webcamStream = stream;
        isStreaming = true;
        
        // Hide idle overlay
        const overlayIdle = document.getElementById("overlayIdle");
        if (overlayIdle) overlayIdle.classList.add("d-none");

        // Start smart processing loop (Non-blocking flow control)
        captureAndSendFrame(); 
        
        // Trigger server-side "start" just to log that session began
        postJSON("/api/camera/start");
        
        // Live Preview Loop (60fps animation)
        const ctx = videoCanvas.getContext('2d');
        function drawPreview() {
            if (!isStreaming) return;
            videoCanvas.width = webcam.videoWidth || 640;
            videoCanvas.height = webcam.videoHeight || 480;
            ctx.drawImage(webcam, 0, 0, videoCanvas.width, videoCanvas.height);
            
            // Draw detection boxes (High Visibility Yellow)
            if (lastBoxes && lastBoxes.length > 0) {
                ctx.strokeStyle = '#ffc107'; // Bootstrap Warning Yellow
                ctx.lineWidth = 4;
                ctx.font = 'bold 20px sans-serif';
                ctx.fillStyle = '#ffc107';
                
                lastBoxes.forEach(box => {
                    const [x1, y1, x2, y2] = box;
                    // Scale boxes if necessary (assuming they are 640x480)
                    const sw = videoCanvas.width / 640;
                    const sh = videoCanvas.height / 480;
                    const rx = x1 * sw;
                    const ry = y1 * sh;
                    const rw = (x2 - x1) * sw;
                    const rh = (y2 - y1) * sh;
                    
                    // Box Fill (Semi-transparent)
                    ctx.globalAlpha = 0.2;
                    ctx.fillRect(rx, ry, rw, rh);
                    ctx.globalAlpha = 1.0;
                    
                    // Box Border
                    ctx.strokeRect(rx, ry, rw, rh);
                    
                    // Label with background
                    const label = 'MOBILE PHONE';
                    const tw = ctx.measureText(label).width;
                    ctx.fillRect(rx, ry > 30 ? ry - 30 : ry, tw + 10, 25);
                    ctx.fillStyle = '#000';
                    ctx.fillText(label, rx + 5, ry > 30 ? ry - 10 : ry + 20);
                    ctx.fillStyle = '#ffc107'; // reset for next box
                });
            }

            // DEBUG OVERLAY
            ctx.fillStyle = 'rgba(0,0,0,0.6)';
            ctx.fillRect(5, 5, 300, 30);
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 12px sans-serif';
            const earVal = lastStatus.ear !== undefined && lastStatus.ear !== null ? ` | EAR: ${lastStatus.ear.toFixed(3)}` : " | FACE LOST";
            const phoneConf = lastStatus.phone_confidence !== undefined ? ` | P: ${(lastStatus.phone_confidence*100).toFixed(0)}%` : "";
            ctx.fillText(`AI ACTIVE | B: ${lastBoxes.length}${earVal}${phoneConf}`, 12, 25);
            
            requestAnimationFrame(drawPreview);
        }
        requestAnimationFrame(drawPreview);
        
    } catch (err) {
        console.error("Camera access error:", err);
        alert("Could not access camera. Please check permissions.");
    }
}

function stopBrowserCamera() {
    isStreaming = false;
    if (streamingInterval) {
        clearInterval(streamingInterval);
        streamingInterval = null;
    }
    if (webcamStream) {
        webcamStream.getTracks().forEach(track => track.stop());
        webcamStream = null;
    }
    
    // Show idle overlay
    const overlayIdle = document.getElementById("overlayIdle");
    if (overlayIdle) overlayIdle.classList.remove("d-none");
    
    // Reset UI
    updateUIWithStatus({ camera_running: false });
    
    // Trigger server-side "stop"
    postJSON("/api/camera/stop");
}

async function captureAndSendFrame() {
    if (!isStreaming) return;
    if (isProcessingFrame) {
        // Server still busy — check again in 30ms instead of queuing a new request
        setTimeout(captureAndSendFrame, 30);
        return;
    }
    
    const webcam = document.getElementById('webcam');
    const captureCanvas = document.getElementById('captureCanvas');
    if (!webcam || !captureCanvas) return;

    // Capture at FULL 640x480 — required for accurate EAR calculation
    captureCanvas.width = 640;
    captureCanvas.height = 480;
    const ctx = captureCanvas.getContext('2d');
    ctx.drawImage(webcam, 0, 0, 640, 480);

    // JPEG quality 0.75 — preserves edge detail for face landmark accuracy
    const dataUrl = captureCanvas.toDataURL('image/jpeg', 0.75);

    let nextDelay = 0; // Fire next frame immediately after server responds
    try {
        isProcessingFrame = true;
        const res = await fetch("/api/process_frame", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image: dataUrl }),
            signal: AbortSignal.timeout(4000)
        });
        if (res.ok) {
            const status = await res.json();
            updateUIWithStatus(status);
            nextDelay = 0;
        }
    } catch (err) {
        console.warn("Frame skipped:", err.message);
        nextDelay = 150;
    } finally {
        isProcessingFrame = false;
        setTimeout(captureAndSendFrame, nextDelay);
    }
}

function updateUIWithStatus(s) {
    const overallText = document.getElementById("overallText");
    if (!overallText) return;

    // FPS (We simulate these for the UI based on our loop)
    const fpsCapture = document.getElementById("fpsCapture");
    const fpsProcess = document.getElementById("fpsProcess");
    if (fpsCapture) fpsCapture.textContent = isStreaming ? "15.0" : "0.0";
    if (fpsProcess) fpsProcess.textContent = isStreaming ? streamFPS.toFixed(1) : "0.0";

    let apiOverallLevel = s.overall_level ?? 0;
    let fallbackMessage = s.overall_message ?? "-";
    let isRunning = s.camera_running ?? false;

    let finalOverallLevel = isRunning ? Math.max(apiOverallLevel, currentSpeedLevel) : 0;
    if (isRunning) {
        if (currentSpeedLevel >= 2 && finalOverallLevel >= 2) {
            fallbackMessage = "CRITICAL: SPEED";
        } else if (currentSpeedLevel >= 1 && finalOverallLevel >= 1 && apiOverallLevel < 2) {
            fallbackMessage = "WARNING: SPEED";
        }
    }

    overallText.textContent = isRunning ? fallbackMessage : "Stopped";
    const overallBadge = document.getElementById("overallBadge");
    if (overallBadge) {
      const ob = badgeForLevel(finalOverallLevel);
      overallBadge.className = `badge rounded-pill px-3 py-2 ${ob.cls}`;
      overallBadge.textContent = isRunning ? ob.text : "STOPPED";
    }

    const dot = document.getElementById("dotOverall");
    setDot(dot, finalOverallLevel);

    const eyesTimer = document.getElementById("eyesTimer");
    const phoneTimer = document.getElementById("phoneTimer");
    if (eyesTimer) eyesTimer.textContent = (s.drowsiness_duration_s ?? 0).toFixed(1);
    if (phoneTimer) phoneTimer.textContent = (s.phone_duration_s ?? 0).toFixed(1);

    const drowsyText = document.getElementById("drowsyText");
    const dBadge = document.getElementById("drowsyBadge");
    if (drowsyText && dBadge) {
      drowsyText.textContent = s.drowsiness_message ?? "-";
      const db = badgeForLevel(s.drowsiness_level ?? 0);
      dBadge.className = `badge rounded-pill ${db.cls}`;
      dBadge.textContent = db.text;
    }

    const phoneText = document.getElementById("phoneText");
    const pBadge = document.getElementById("phoneBadge");
    if (phoneText && pBadge) {
      phoneText.textContent = s.phone_message ?? "-";
      const pb = badgeForLevel(s.phone_level ?? 0);
      pBadge.className = `badge rounded-pill ${pb.cls}`;
      pBadge.textContent = pb.text;
    }

    const earVal = document.getElementById("earVal");
    const phoneConfVal = document.getElementById("phoneConfVal");
    if (earVal) earVal.textContent = (s.ear === null || s.ear === undefined) ? "-" : Number(s.ear).toFixed(3);
    if (phoneConfVal) phoneConfVal.textContent = Number(s.phone_conf ?? 0).toFixed(2);

    const critOverlay = document.getElementById("overlayCritical");
    if (critOverlay) {
      if ((s.phone_level ?? 0) >= 3) critOverlay.classList.remove("d-none");
      else critOverlay.classList.add("d-none");
    }

    if ((s.voice_token ?? 0) > lastVoiceToken) {
      lastVoiceToken = s.voice_token;
      speak(s.voice_text ?? "");
    }

    // Update global markers for rendering loop
    lastStatus = s;
    lastBoxes = s.phone_boxes || [];
}

function initUI() {
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  
  // Use Browser-Side Capture for Cloud Compatibility
  if (btnStart) btnStart.onclick = startBrowserCamera;
  if (btnStop) btnStop.onclick = stopBrowserCamera;


  const speedModeSelect = document.getElementById("speedMode");
  if (speedModeSelect) {
    speedModeSelect.onchange = (e) => {
      speedMode = e.target.value;
      if (speedMode === "gps") {
        currentSpeed = 0;
        lastSpeedVal = 0;
        if ("geolocation" in navigator) {
          watchId = navigator.geolocation.watchPosition(
            (pos) => { 
                if (pos && pos.coords && pos.coords.speed !== null) {
                    currentSpeed = pos.coords.speed * 3.6; 
                } else {
                    currentSpeed = 0;
                }
            },
            null,
            { enableHighAccuracy: true }
          );
        }
      } else if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
      }
    };
  }

  const btnApplyOrg = document.getElementById("btnApplyOrg");
  if (btnApplyOrg) {
    btnApplyOrg.onclick = async () => {
      const orgId = document.getElementById("applyOrgId")?.value;
      if(!orgId) return alert("Please select an organisation");
      btnApplyOrg.disabled = true;
      try {
        const res = await fetch('/api/driver/apply', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({org_id: parseInt(orgId)})
        });
        if(res.ok) { alert("Applied!"); window.location.reload(); }
        else alert("Failed");
      } catch(e) { alert("Error"); }
      btnApplyOrg.disabled = false;
    };
  }

  // Driver Chat
  const driverChatInput = document.getElementById("driverChatInput");
  const btnDriverSend = document.getElementById("btnDriverSend");
  if (driverChatInput && btnDriverSend) {
    btnDriverSend.onclick = async () => {
      const msg = driverChatInput.value.trim();
      if(!msg) return;
      await sendDriverChatMessage(msg, 'text');
      driverChatInput.value = '';
    };
    driverChatInput.onkeypress = (e) => {
      if (e.key === 'Enter') btnDriverSend.click();
    };
  }

  const driverImageInput = document.getElementById('driverImageInput');
  if(driverImageInput) {
      driverImageInput.onchange = async (e) => {
          const file = e.target.files[0];
          if (!file) return;
          const uploadRes = await uploadChatFile(file);
          if (uploadRes.ok) {
              let type = 'document';
              const ext = file.name.split('.').pop().toLowerCase();
              if (['png','jpg','jpeg','gif','webp','heic'].includes(ext)) type = 'image';
              else if (['webm','mp4','mov','avi','mkv'].includes(ext)) type = 'video';
              else if (['wav','mp3','ogg'].includes(ext)) type = 'voice';
              await sendDriverChatMessage(uploadRes.filepath, type);
          }
          e.target.value = '';
      };
  }

  const btnDriverRecord = document.getElementById('btnDriverRecord');
  if(btnDriverRecord) {
      // Local closure for recorder
      let driverMediaRecorder;
      let driverAudioChunks = [];
      btnDriverRecord.onclick = async () => {
          if (!driverMediaRecorder || driverMediaRecorder.state === 'inactive') {
              try {
                  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                  driverMediaRecorder = new MediaRecorder(stream);
                  driverAudioChunks = [];
                  driverMediaRecorder.ondataavailable = e => driverAudioChunks.push(e.data);
                  driverMediaRecorder.onstop = async () => {
                      const blob = new Blob(driverAudioChunks, { type: 'audio/webm' });
                      const file = new File([blob], "voice.webm", { type: "audio/webm" });
                      const uploadRes = await uploadChatFile(file);
                      if (uploadRes.ok) await sendDriverChatMessage(uploadRes.filepath, 'voice');
                      btnDriverRecord.innerHTML = '<i class="fas fa-microphone"></i>';
                      btnDriverRecord.classList.replace('btn-danger', 'btn-outline-secondary');
                  };
                  driverMediaRecorder.start();
                  btnDriverRecord.innerHTML = '<i class="fas fa-stop"></i>';
                  btnDriverRecord.classList.replace('btn-outline-secondary', 'btn-danger');
              } catch(e) { alert("Microphone error."); }
          } else { driverMediaRecorder.stop(); }
      };
  }
}

function togglePassword(btn, id) {
    const input = document.getElementById(id);
    const icon = btn.querySelector('i');
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.replace('fa-eye', 'fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.replace('fa-eye-slash', 'fa-eye');
    }
}

document.addEventListener("DOMContentLoaded", () => {
  requestNotificationPermission();
  initTheme();
  initUI();
  
  if (document.getElementById("overallText")) {
    pollStatus();
    setInterval(pollStatus, 3000);
  }
  if (document.getElementById("speedometerText")) {
    updateSpeedometer();
    setInterval(updateSpeedometer, 1000);
  }
  
  if (document.getElementById("driverChatBox")) {
    setInterval(fetchDriverChat, 2000);
    fetchDriverChat();
  }

  if (document.getElementById("eventLogBody")) {
    setInterval(fetchDriverEvents, 3000);
    fetchDriverEvents();
  }
  
  // Status broadcast for dash
  if (document.getElementById("speedMode")) {
    setInterval(async () => {
        try {
            await fetch('/api/driver/status', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    speed: Math.round(currentSpeed || 0),
                    drowsiness: document.getElementById("drowsyText")?.textContent || "ALERT",
                    phone: document.getElementById("phoneText")?.textContent || "CLEAR",
                    risk_score: currentSpeedLevel,
                    overall: document.getElementById("overallText")?.textContent || "-"
                })
            });
        } catch(e) {}
    }, 5000);
  }
  
  if (document.getElementById("notifBtn")) {
    setInterval(fetchNotifications, 5000);
    fetchNotifications();
  }

  // Bootstrap Toast Auto-init
  const toastEls = document.querySelectorAll('.toast.show');
  toastEls.forEach(el => {
    try {
        const t = new bootstrap.Toast(el, { autohide: true, delay: 2000 });
        t.show();
    } catch(e) {}
  });
});
