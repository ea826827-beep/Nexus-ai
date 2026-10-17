/* ==========================================
   NEXUS BRAIN AI
   Step 3: script.js
   Flask Backend Integration
========================================== */

"use strict";

/* ==========================================
   1. BACKEND CONFIGURATION
========================================== */

// এখানে তোমার বর্তমান Flask Backend-এর URL বসাবে।
// উদাহরণ: https://your-backend.example.com
// শেষে /chat বা অন্য API path লিখবে না।

const API_BASE_URL = "YOUR_BACKEND_URL";

const CHAT_ENDPOINT = "/chat";
const HEALTH_ENDPOINT = "/health";
const PDF_UPLOAD_ENDPOINT = "/upload-pdf";
const PDF_LIST_ENDPOINT = "/pdfs";

const STORAGE_KEY = "nexus_brain_chats_v1";
const SESSION_KEY = "nexus_brain_session_v1";


/* ==========================================
   2. DOM ELEMENTS
========================================== */

const $ = (selector) => document.querySelector(selector);

const chatForm = $("#chatForm");
const userInput = $("#userInput");
const sendBtn = $("#sendBtn");
const sendIcon = $("#sendIcon");

const chatArea = $("#chatArea");
const messagesContainer = $("#messages");
const welcomeScreen = $("#welcomeScreen");

const newChatBtn = $("#newChatBtn");
const dashboardNewChat = $("#dashboardNewChat");

const menuBtn = $("#menuBtn");
const closeSidebarBtn = $("#closeSidebar");
const sidebar = $("#sidebar");
const sidebarOverlay = $("#sidebarOverlay");

const sidebarStatus = $("#sidebarStatus");
const dashboardStatus = $("#dashboardStatus");
const settingsStatus = $("#settingsStatus");

const totalMessages = $("#totalMessages");
const totalChats = $("#totalChats");

const historyList = $("#historyList");

const documentInput = $("#documentInput");
const documentStatus = $("#documentStatus");

const clearHistoryBtn = $("#clearHistoryBtn");
const toastElement = $("#toast");


/* ==========================================
   3. APP STATE
========================================== */

let isSending = false;
let isUploading = false;
let toastTimer = null;

let currentSessionId = createId();
let currentMessages = [];

let chats = loadChats();


function createId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }

    return "session-" +
        Date.now() +
        "-" +
        Math.random().toString(36).slice(2, 10);
}


/* ==========================================
   4. STORAGE
========================================== */

function loadChats() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);

        if (!saved) {
            return [];
        }

        const parsed = JSON.parse(saved);

        if (!Array.isArray(parsed)) {
            return [];
        }

        return parsed.filter(chat =>
            chat &&
            typeof chat.id === "string" &&
            Array.isArray(chat.messages)
        );

    } catch (error) {
        console.error("Could not load chat history:", error);
        return [];
    }
}


function saveChats() {
    try {
        localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify(chats)
        );

        return true;

    } catch (error) {
        console.error("Could not save chat history:", error);

        showToast("Chat save করা যায়নি। Browser storage পরীক্ষা করো।");

        return false;
    }
}


function saveCurrentSession() {
    if (currentMessages.length === 0) {
        return;
    }

    const index = chats.findIndex(
        chat => chat.id === currentSessionId
    );

    const existing = index !== -1 ? chats[index] : null;

    const firstUserMessage = currentMessages.find(
        message => message.role === "user"
    );

    const title = firstUserMessage
        ? firstUserMessage.content.slice(0, 65)
        : "New Chat";

    const updatedChat = {
        id: currentSessionId,
        title: title,
        updatedAt: new Date().toISOString(),
        messages: currentMessages
    };

    if (index !== -1) {
        chats[index] = updatedChat;
    } else {
        chats.unshift(updatedChat);
    }

    // সর্বোচ্চ 50টি conversation রাখা হবে।
    chats = chats
        .sort((a, b) =>
            new Date(b.updatedAt) - new Date(a.updatedAt)
        )
        .slice(0, 50);

    saveChats();
    updateDashboard();
    renderHistory();
}


/* ==========================================
   5. TOAST NOTIFICATIONS
========================================== */

function showToast(message) {
    if (!toastElement) {
        alert(message);
        return;
    }

    toastElement.textContent = message;
    toastElement.classList.add("show");

    clearTimeout(toastTimer);

    toastTimer = setTimeout(() => {
        toastElement.classList.remove("show");
    }, 3000);
}


/* ==========================================
   6. BACKEND URL VALIDATION
========================================== */

function getApiBase() {
    const value = API_BASE_URL.trim();

    if (
        !value ||
        value === "YOUR_BACKEND_URL" ||
        value.includes("YOUR_BACKEND")
    ) {
        throw new Error(
            "প্রথমে script.js-এ তোমার Flask Backend URL বসাও।"
        );
    }

    const parsed = new URL(value);

    if (!["https:", "http:"].includes(parsed.protocol)) {
        throw new Error("Backend URL সঠিক নয়।");
    }

    // Public website-এ HTTP backend browser block করতে পারে।
    if (
        window.location.protocol === "https:" &&
        parsed.protocol !== "https:"
    ) {
        throw new Error(
            "HTTPS Frontend-এর জন্য HTTPS Backend URL ব্যবহার করো।"
        );
    }

    return value.replace(/\/+$/, "");
}


function apiUrl(endpoint) {
    return getApiBase() + endpoint;
}


/* ==========================================
   7. BACKEND HEALTH CHECK
========================================== */

async function checkBackend() {
    const statusElements = [
        sidebarStatus,
        dashboardStatus,
        settingsStatus
    ].filter(Boolean);

    statusElements.forEach(element => {
        element.textContent = "Checking...";
    });

    const dot = $(".status-dot");

    if (dot) {
        dot.classList.remove("online", "offline");
    }

    try {
        const response = await fetch(
            apiUrl(HEALTH_ENDPOINT),
            {
                method: "GET",
                headers: {
                    "Accept": "application/json"
                },
                signal: AbortSignal.timeout(12000)
            }
        );

        if (!response.ok) {
            throw new Error("Backend HTTP " + response.status);
        }

        const data = await response.json();

        // Health endpoint থেকে valid JSON পাওয়া গেছে।
        statusElements.forEach(element => {
            element.textContent = "Connected";
        });

        if (dot) {
            dot.classList.add("online");
        }

        // API-তে model_loaded থাকলে সেটিও পরীক্ষা করা হবে।
        if (data.model_loaded === false) {
            statusElements.forEach(element => {
                element.textContent = "Model Loading";
            });

            if (dot) {
                dot.classList.remove("online");
            }

            showToast("Backend চলছে, কিন্তু AI Model এখনও প্রস্তুত নয়।");

            return data;
        }

        console.log("NEXUS Backend:", data);

        return data;

    } catch (error) {
        console.error("Backend health check failed:", error);

        statusElements.forEach(element => {
            element.textContent = "Offline";
        });

        if (dot) {
            dot.classList.add("offline");
        }

        return null;
    }
}


/* ==========================================
   8. MESSAGE RENDERING
========================================== */

function renderMessage(role, content) {
    const message = document.createElement("article");

    message.className = "message " +
        (role === "user"
            ? "user-message"
            : "assistant-message");

    const avatar = document.createElement("div");

    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "U" : "N";

    const body = document.createElement("div");
    body.className = "message-body";

    const label = document.createElement("div");
    label.className = "message-label";

    label.textContent = role === "user"
        ? "You"
        : "NEXUS BRAIN";

    const text = document.createElement("div");
    text.className = "message-content";

    // নিরাপদভাবে text দেখানো হচ্ছে।
    // AI-এর উত্তরে থাকা HTML execute হবে না।
    text.textContent = String(content ?? "");

    body.appendChild(label);
    body.appendChild(text);

    if (role === "assistant") {
        const actions = document.createElement("div");

        actions.className = "message-actions";

        const copyBtn = document.createElement("button");

        copyBtn.type = "button";
        copyBtn.textContent = "Copy Answer";

        copyBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(
                    String(content ?? "")
                );

                showToast("উত্তর কপি হয়েছে!");

            } catch (error) {
                showToast(
                    "Copy করা যায়নি। উত্তরটি নির্বাচন করে কপি করো।"
                );
            }
        });

        actions.appendChild(copyBtn);
        body.appendChild(actions);
    }

    message.appendChild(avatar);
    message.appendChild(body);

    messagesContainer.appendChild(message);

    return message;
}


function renderCurrentMessages() {
    messagesContainer.replaceChildren();

    currentMessages.forEach(message => {
        renderMessage(message.role, message.content);
    });

    updateWelcomeScreen();
    scrollToBottom();
}


function updateWelcomeScreen() {
    if (!welcomeScreen) {
        return;
    }

    welcomeScreen.style.display =
        currentMessages.length === 0 ? "" : "none";
}


function scrollToBottom() {
    requestAnimationFrame(() => {
        if (chatArea) {
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    });
}


/* ==========================================
   9. LOADING INDICATOR
========================================== */

function showTypingIndicator() {
    const wrapper = document.createElement("article");

    wrapper.className = "message assistant-message";
    wrapper.id = "typingIndicator";

    const avatar = document.createElement("div");

    avatar.className = "message-avatar";
    avatar.textContent = "N";

    const body = document.createElement("div");
    body.className = "message-body";

    const label = document.createElement("div");

    label.className = "message-label";
    label.textContent = "NEXUS BRAIN";

    const typing = document.createElement("div");

    typing.className = "typing-indicator";

    for (let i = 0; i < 3; i++) {
        typing.appendChild(document.createElement("span"));
    }

    body.appendChild(label);
    body.appendChild(typing);

    wrapper.appendChild(avatar);
    wrapper.appendChild(body);

    messagesContainer.appendChild(wrapper);

    scrollToBottom();

    return wrapper;
}


/* ==========================================
   10. CHAT API
========================================== */

async function sendMessage(message) {
    if (isSending || isUploading) {
        return;
    }

    const prompt = String(message || "").trim();

    if (!prompt) {
        return;
    }

    if (prompt.length > 10000) {
        showToast("Message অনেক বড়।");
        return;
    }

    isSending = true;

    sendBtn.disabled = true;
    userInput.disabled = true;

    const userMessage = {
        role: "user",
        content: prompt
    };

    currentMessages.push(userMessage);

    renderMessage("user", prompt);

    updateWelcomeScreen();
    saveCurrentSession();

    userInput.value = "";
    userInput.style.height = "auto";

    const loading = showTypingIndicator();

    let answer = "";

    try {
        const response = await fetch(
            apiUrl(CHAT_ENDPOINT),
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },

                body: JSON.stringify({
                    message: prompt,
                    session_id: currentSessionId,
                    web_search: false
                }),

                signal: AbortSignal.timeout(180000)
            }
        );

        let data;

        try {
            data = await response.json();
        } catch (error) {
            throw new Error(
                "Backend থেকে JSON response আসেনি।"
            );
        }

        if (!response.ok) {
            throw new Error(
                data.error ||
                data.message ||
                "Backend Error: HTTP " + response.status
            );
        }

        if (typeof data.answer !== "string") {
            throw new Error(
                data.error ||
                "Backend response-এ answer পাওয়া যায়নি।"
            );
        }

        answer = data.answer;

        currentMessages.push({
            role: "assistant",
            content: answer
        });

        console.log("Chat response:", {
            used_pdf_context: data.used_pdf_context,
            web_search_requested: data.web_search_requested
        });

    } catch (error) {
        console.error("Chat request failed:", error);

        answer = "দুঃখিত, AI-এর সঙ্গে সংযোগ করা যায়নি।\n\n" +
            error.message +
            "\n\nBackend চালু আছে কি না এবং API URL সঠিক কি না পরীক্ষা করো।";

        // Error-ও history-তে থাকবে।
        currentMessages.push({
            role: "assistant",
            content: answer
        });

    } finally {
        loading.remove();

        renderMessage("assistant", answer);

        saveCurrentSession();

        isSending = false;

        sendBtn.disabled = false;
        userInput.disabled = false;

        userInput.focus();

        updateDashboard();
        scrollToBottom();
    }
}


/* ==========================================
   11. CHAT FORM
========================================== */

chatForm.addEventListener("submit", async event => {
    event.preventDefault();

    await sendMessage(userInput.value);
});


userInput.addEventListener("input", () => {
    userInput.style.height = "auto";

    userInput.style.height =
        Math.min(userInput.scrollHeight, 160) + "px";
});


userInput.addEventListener("keydown", event => {
    if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.isComposing
    ) {
        event.preventDefault();

        chatForm.requestSubmit();
    }
});


/* ==========================================
   12. SUGGESTION CARDS
========================================== */

document.querySelectorAll("[data-prompt]").forEach(button => {
    button.addEventListener("click", () => {
        const prompt = button.dataset.prompt;

        if (!prompt) {
            return;
        }

        navigateTo("chat");

        sendMessage(prompt);
    });
});


/* ==========================================
   13. NEW CHAT
========================================== */

function newChat() {
    if (isSending || isUploading) {
        showToast("বর্তমান কাজ শেষ হলে নতুন Chat শুরু করো।");
        return;
    }

    currentSessionId = createId();
    currentMessages = [];

    messagesContainer.replaceChildren();

    userInput.value = "";
    userInput.style.height = "auto";

    updateWelcomeScreen();

    closeSidebar();

    navigateTo("chat");

    updateDashboard();

    userInput.focus();
}


if (newChatBtn) {
    newChatBtn.addEventListener("click", newChat);
}


if (dashboardNewChat) {
    dashboardNewChat.addEventListener("click", newChat);
}


/* ==========================================
   14. SIDEBAR NAVIGATION
========================================== */

const pageMap = {
    chat: {
        id: "chatPage",
        title: "AI Assistant"
    },

    dashboard: {
        id: "dashboardPage",
        title: "Dashboard"
    },

    history: {
        id: "historyPage",
        title: "Chat History"
    },

    documents: {
        id: "documentsPage",
        title: "Documents"
    },

    settings: {
        id: "settingsPage",
        title: "Settings"
    }
};


function navigateTo(pageName) {
    const page = pageMap[pageName];

    if (!page) {
        return;
    }

    document.querySelectorAll(".page").forEach(element => {
        element.classList.remove("active");
    });

    const selectedPage = document.getElementById(page.id);

    if (selectedPage) {
        selectedPage.classList.add("active");
    }

    document.querySelectorAll(".nav-item").forEach(button => {
        button.classList.toggle(
            "active",
            button.dataset.page === pageName
        );
    });

    const title = $("#pageTitle");

    if (title) {
        title.textContent = page.title;
    }

    closeSidebar();

    if (pageName === "dashboard") {
        updateDashboard();
    }

    if (pageName === "history") {
        renderHistory();
    }

    if (pageName === "documents") {
        checkPdfList();
    }
}


document.querySelectorAll(".nav-item[data-page]").forEach(button => {
    button.addEventListener("click", () => {
        navigateTo(button.dataset.page);
    });
});


/* ==========================================
   15. MOBILE SIDEBAR
========================================== */

function openSidebar() {
    sidebar.classList.add("open");
    sidebarOverlay.classList.add("visible");

    document.body.style.overflow = "hidden";
}


function closeSidebar() {
    sidebar.classList.remove("open");
    sidebarOverlay.classList.remove("visible");

    document.body.style.overflow = "";
}


if (menuBtn) {
    menuBtn.addEventListener("click", openSidebar);
}


if (closeSidebarBtn) {
    closeSidebarBtn.addEventListener("click", closeSidebar);
}


if (sidebarOverlay) {
    sidebarOverlay.addEventListener("click", closeSidebar);
}


document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
        closeSidebar();
    }
});


/* ==========================================
   16. CHAT HISTORY
========================================== */

function renderHistory() {
    if (!historyList) {
        return;
    }

    historyList.replaceChildren();

    if (chats.length === 0) {
        const empty = document.createElement("div");

        empty.className = "history-empty";
        empty.textContent =
            "এখনও কোনো conversation save হয়নি।";

        historyList.appendChild(empty);

        return;
    }

    chats.forEach(chat => {
        const item = document.createElement("div");

        item.className = "history-item";

        const info = document.createElement("div");
        info.style.minWidth = "0";
        info.style.flex = "1";

        const openButton = document.createElement("button");

        openButton.type = "button";
        openButton.textContent = chat.title || "New Chat";

        openButton.addEventListener("click", () => {
            openChat(chat.id);
        });

        const date = document.createElement("small");

        date.textContent = formatDate(chat.updatedAt);

        info.appendChild(openButton);
        info.appendChild(date);

        const deleteButton = document.createElement("button");

        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.setAttribute(
            "aria-label",
            "Delete conversation"
        );

        deleteButton.addEventListener("click", () => {
            deleteChat(chat.id);
        });

        item.appendChild(info);
        item.appendChild(deleteButton);

        historyList.appendChild(item);
    });
}


function openChat(chatId) {
    if (isSending || isUploading) {
        showToast("বর্তমান কাজ শেষ হলে Chat বদলাও।");
        return;
    }

    const chat = chats.find(item => item.id === chatId);

    if (!chat) {
        showToast("Chat পাওয়া যায়নি।");
        return;
    }

    currentSessionId = chat.id;

    currentMessages = chat.messages.map(message => ({
        role: message.role,
        content: String(message.content ?? "")
    }));

    renderCurrentMessages();

    navigateTo("chat");

    showToast("পুরনো Chat খোলা হয়েছে।");
}


function deleteChat(chatId) {
    const confirmed = confirm(
        "এই conversation মুছে ফেলতে চাও?"
    );

    if (!confirmed) {
        return;
    }

    chats = chats.filter(chat => chat.id !== chatId);

    saveChats();

    if (currentSessionId === chatId) {
        currentSessionId = createId();
        currentMessages = [];

        renderCurrentMessages();
    }

    renderHistory();
    updateDashboard();

    showToast("Chat History থেকে মুছে ফেলা হয়েছে।");
}


function formatDate(value) {
    try {
        return new Date(value).toLocaleString();
    } catch (error) {
        return "";
    }
}


/* ==========================================
   17. DASHBOARD
========================================== */

function updateDashboard() {
    if (totalMessages) {
        totalMessages.textContent = chats.reduce(
            (sum, chat) => sum + chat.messages.length,
            0
        ) + currentMessages.length;
    }

    if (totalChats) {
        const currentSaved = chats.some(
            chat => chat.id === currentSessionId
        );

        totalChats.textContent =
            chats.length +
            (
                currentMessages.length > 0 && !currentSaved
                    ? 1
                    : 0
            );
    }
}


/* ==========================================
   18. CLEAR CHAT HISTORY
========================================== */

if (clearHistoryBtn) {
    clearHistoryBtn.addEventListener("click", () => {
        const confirmed = confirm(
            "সব locally saved Chat History মুছে ফেলতে চাও?"
        );

        if (!confirmed) {
            return;
        }

        chats = [];

        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch (error) {
            console.error(error);
        }

        currentSessionId = createId();
        currentMessages = [];

        renderCurrentMessages();
        renderHistory();
        updateDashboard();

        showToast("Local Chat History clear করা হয়েছে।");
    });
}


/* ==========================================
   19. PDF UPLOAD
========================================== */

if (documentInput) {
    documentInput.addEventListener("change", async () => {
        const file = documentInput.files?.[0];

        if (!file) {
            return;
        }

        if (isUploading || isSending) {
            showToast("বর্তমান কাজ শেষ হলে PDF upload করো।");

            documentInput.value = "";

            return;
        }

        const maxSize = 10 * 1024 * 1024;

        if (file.size > maxSize) {
            showToast("ফাইলটি 10 MB-এর চেয়ে ছোট হতে হবে।");

            documentInput.value = "";

            return;
        }

        if (!file.name.toLowerCase().endsWith(".pdf")) {
            showToast("এই Upload API-তে PDF ফাইল নির্বাচন করো।");

            documentInput.value = "";

            return;
        }

        isUploading = true;

        documentStatus.textContent = "Uploading PDF...";

        try {
            const formData = new FormData();

            formData.append("file", file);

            const response = await fetch(
                apiUrl(PDF_UPLOAD_ENDPOINT),
                {
                    method: "POST",
                    body: formData,
                    signal: AbortSignal.timeout(120000)
                }
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error ||
                    data.message ||
                    "PDF upload failed: HTTP " + response.status
                );
            }

            documentStatus.textContent =
                data.message ||
                "PDF upload request সফল হয়েছে।";

            showToast("PDF Backend-এ পাঠানো হয়েছে।");

            console.log("PDF upload result:", data);

        } catch (error) {
            console.error("PDF upload failed:", error);

            documentStatus.textContent =
                "Upload failed: " + error.message;

            showToast("PDF upload হয়নি। Backend পরীক্ষা করো।");

        } finally {
            isUploading = false;

            documentInput.value = "";
        }
    });
}


/* ==========================================
   20. PDF LIST
========================================== */

async function checkPdfList() {
    try {
        const response = await fetch(
            apiUrl(PDF_LIST_ENDPOINT),
            {
                method: "GET",
                headers: {
                    "Accept": "application/json"
                },
                signal: AbortSignal.timeout(10000)
            }
        );

        if (!response.ok) {
            throw new Error("PDF list unavailable");
        }

        const data = await response.json();

        console.log("Backend PDF information:", data);

    } catch (error) {
        // PDF list endpoint না থাকলেও Chat চলতে পারবে।
        console.warn("PDF list check:", error.message);
    }
}


/* ==========================================
   21. INITIALIZE APPLICATION
========================================== */

function initializeApp() {
    renderCurrentMessages();

    renderHistory();

    updateDashboard();

    // Backend-এর সঙ্গে যোগাযোগ পরীক্ষা।
    checkBackend();

    // প্রতি 60 সেকেন্ডে status পরীক্ষা।
    setInterval(() => {
        checkBackend();
    }, 60000);

    console.log("NEXUS BRAIN Frontend initialized.");
}


initializeApp();

































  