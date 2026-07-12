const STORAGE_KEYS = {
  language: "pos-language",
  theme: "pos-theme",
};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const finePointer = window.matchMedia("(pointer: fine)");
const systemTheme = window.matchMedia("(prefers-color-scheme: light)");

const translations = {
  ru: {
    pageTitle: "P.OS | Provision Operating System",
    metaDescription: "P.OS — AI-менеджер, администратор и система безопасности для Discord-серверов.",
    skip: "Перейти к содержимому",
    navAria: "Основная навигация",
    menuAria: "Открыть меню",
    settingsAria: "Настройки сайта",
    languageAria: "Язык",
    themeAria: "Тема",
    navAi: "Интеллект",
    navSecurity: "Безопасность",
    navControl: "Управление",
    navToolkit: "Возможности",
    themeDark: "Dark",
    themeLight: "Light",
    systemName: "Provision Operating System",
    heroLine: "ИИ-менеджер Discord: понимает, защищает, управляет и помнит.",
    heroPrimary: "Увидеть P.OS в работе",
    heroSecondary: "Возможности",
    consoleAria: "Интерактивная демонстрация P.OS",
    consoleTabsAria: "Режимы консоли",
    modeOverview: "Состояние системы",
    modeSecurity: "Безопасность",
    modeOperator: "Оператор",
    modeMemory: "Память и события",
    consoleSummary: "Контекст принят. Система готова к работе.",
    activityTitle: "Контуры",
    authorityTitle: "Полномочия",
    authorityText: "Действия защищены проверкой доступа.",
    ready: "Готов",
    capabilityTitle: "Один P.OS. Весь контур сервера.",
    capAi: "ИИ-менеджер",
    capAiText: "Контекстный диалог и работа с медиа.",
    capMod: "Модерация",
    capModText: "Спам, ссылки, вложения и антирейд.",
    capAdmin: "Администрирование",
    capAdminText: "Участники, роли, каналы и настройки.",
    capForms: "Формы",
    capFormsText: "Заявки, жалобы и работа команды сервера.",
    capMemory: "Память",
    capMemoryText: "Проверяемые события, пинги и действия.",
    capMedia: "Медиа",
    capMediaText: "Изображения, видео и адаптивное создание GIF.",
    intelligenceTitle: "Не команда.\nНамерение.",
    intelligenceText: "P.OS понимает обычную речь, уточняет цель и связывает запрос с реальным состоянием сервера.",
    dialogueLabel: "КАНАЛ ВЛАДЕЛЬЦА // ДЕМО",
    promptExamplesAria: "Примеры запросов P.OS",
    promptAudit: "Кто удалил сообщение?",
    promptRole: "Выдай роль участнику",
    promptRaid: "Проверь всплеск входов",
    creatorLabel: "Пумба",
    ecosystemTitle: "Сервер как единая система",
    ecosystemText: "P.OS связывает разговор, состояние Discord и подтверждённые действия в один понятный процесс.",
    ecosystemAria: "Экосистема P.OS",
    logoAlt: "Графический знак P.OS",
    nodeUsers: "Участники",
    nodeChannels: "Каналы",
    nodeRoles: "Роли",
    nodeSettings: "Настройки",
    nodeEvents: "События",
    nodeForms: "Формы",
    flowAria: "Цикл работы P.OS",
    flowMessage: "Сообщение",
    flowContext: "Контекст",
    flowCheck: "Проверка",
    flowAction: "Действие",
    flowLog: "Журнал",
    securityTitle: "Защита до действия",
    securityText: "ИИ усиливает модерацию, но не получает право обходить проверяемые правила и полномочия Discord.",
    layerRules: "Сигналы",
    layerRulesText: "Ссылки, спам, массовые упоминания, вложения и рейд-паттерны.",
    layerAlways: "Всегда",
    layerAi: "AI-проверка",
    layerAiText: "Текст и медиа получают дополнительную оценку без слепого наказания.",
    layerContext: "По контексту",
    layerAuthority: "Полномочия",
    layerAuthorityText: "Права, иерархия и подтверждение проверяются вне модели.",
    layerVerified: "Проверено",
    operatorTitle: "Управление словами",
    operatorText: "От участника до структуры сервера: P.OS превращает запрос в проверяемый план действий.",
    scopeAria: "Режим управления сервером",
    scopeMembers: "Участники",
    scopeStructure: "Структура",
    stepResolve: "Определить цель",
    stepResolveText: "Пользователь, роль или канал сопоставляются с объектом Discord.",
    stepCheck: "Проверить возможность",
    stepCheckText: "P.OS сверяет права, иерархию и защищённые цели.",
    stepConfirm: "Подтвердить изменение",
    stepConfirmText: "Критичное действие не выполняется только на слове модели.",
    stepRecord: "Записать результат",
    stepRecordText: "Успех или отказ остаются в журнале действий.",
    memoryTitle: "Факты не исчезают вместе с сообщением",
    memoryText: "P.OS хранит проверяемый журнал событий и отделяет найденные данные от предположений.",
    memoryQuery: "Кто упомянул мою роль, а затем удалил сообщение?",
    searchAria: "Показать или скрыть найденные события",
    eventMention: "Упоминание роли зафиксировано",
    eventMentionText: "Источник: журнал событий сервера",
    eventDeleted: "Исходное сообщение удалено",
    eventDeletedText: "Событие связано с сохранённой записью упоминания",
    evidenceLabel: "Ответ P.OS",
    evidenceText: "Факт найден в журнале. Удаление сообщения не удалило запись события.",
    toolkitTitle: "Один интерфейс.\nМного задач.",
    toolkitText: "P.OS объединяет ежедневные инструменты сервера, чтобы запрос не приходилось раскладывать на десяток отдельных команд.",
    toolkitAria: "Индекс возможностей P.OS",
    toolkitLabel: "P.OS // FUNCTION INDEX",
    toolkitReady: "Все контуры готовы",
    toolDialogue: "Диалог",
    toolDialogueText: "Контекст, изображения, видео и естественный язык.",
    toolModeration: "Модерация",
    toolModerationText: "Проверка риска, антирейд и обоснованные действия.",
    toolControl: "Управление",
    toolControlText: "Участники, роли, каналы, права и настройки.",
    toolMemory: "Память",
    toolMemoryText: "События, пинги, удалённые сообщения и история действий.",
    toolForms: "Формы",
    toolFormsText: "Заявки, жалобы и закрытые процессы команды.",
    toolMedia: "Медиа",
    toolMediaText: "Адаптивная сборка GIF из изображений и видео.",
    closingTitle: "Не ещё один бот.\nОперационный слой Discord.",
    closingText: "P.OS создан Пумбой и развивается как единая система для разговора, защиты и управления сервером.",
    exploreCta: "Посмотреть P.OS в работе",
    footerText: "Provision Operating System. Создан Пумбой.",
    backTop: "Наверх ↑",
  },
  en: {
    pageTitle: "P.OS | Provision Operating System",
    metaDescription: "P.OS is an AI manager, administrator and security system for Discord servers.",
    skip: "Skip to content",
    navAria: "Primary navigation",
    menuAria: "Open menu",
    settingsAria: "Site settings",
    languageAria: "Language",
    themeAria: "Theme",
    navAi: "Intelligence",
    navSecurity: "Security",
    navControl: "Control",
    navToolkit: "Capabilities",
    themeDark: "Dark",
    themeLight: "Light",
    systemName: "Provision Operating System",
    heroLine: "A Discord AI manager that understands, protects, operates and remembers.",
    heroPrimary: "See P.OS in action",
    heroSecondary: "Capabilities",
    consoleAria: "Interactive P.OS preview",
    consoleTabsAria: "Console modes",
    modeOverview: "System overview",
    modeSecurity: "Security",
    modeOperator: "Operator",
    modeMemory: "Memory and events",
    consoleSummary: "Context received. The system is ready.",
    activityTitle: "Systems",
    authorityTitle: "Authority",
    authorityText: "Actions are protected by access checks.",
    ready: "Ready",
    capabilityTitle: "One P.OS. The whole server surface.",
    capAi: "AI manager",
    capAiText: "Contextual conversation and media understanding.",
    capMod: "Moderation",
    capModText: "Spam, links, attachments and anti-raid.",
    capAdmin: "Administration",
    capAdminText: "Members, roles, channels and settings.",
    capForms: "Forms",
    capFormsText: "Applications, reports and staff workflows.",
    capMemory: "Memory",
    capMemoryText: "Verifiable events, mentions and actions.",
    capMedia: "Media",
    capMediaText: "Images, video and adaptive GIF creation.",
    intelligenceTitle: "Not a command.\nAn intent.",
    intelligenceText: "P.OS understands natural language, clarifies the goal and connects each request to the server's real state.",
    dialogueLabel: "OWNER CHANNEL // DEMO",
    promptExamplesAria: "P.OS request examples",
    promptAudit: "Who deleted the message?",
    promptRole: "Assign a role to a member",
    promptRaid: "Check the join spike",
    creatorLabel: "Pumba",
    ecosystemTitle: "A server as one system",
    ecosystemText: "P.OS connects conversation, Discord state and confirmed actions into one understandable process.",
    ecosystemAria: "P.OS ecosystem",
    logoAlt: "P.OS graphic mark",
    nodeUsers: "Members",
    nodeChannels: "Channels",
    nodeRoles: "Roles",
    nodeSettings: "Settings",
    nodeEvents: "Events",
    nodeForms: "Forms",
    flowAria: "P.OS operating cycle",
    flowMessage: "Message",
    flowContext: "Context",
    flowCheck: "Validation",
    flowAction: "Action",
    flowLog: "Journal",
    securityTitle: "Protection before action",
    securityText: "AI strengthens moderation, but it cannot bypass verifiable rules or Discord authority.",
    layerRules: "Signals",
    layerRulesText: "Links, spam, mass mentions, attachments and raid patterns.",
    layerAlways: "Always on",
    layerAi: "AI review",
    layerAiText: "Text and media receive additional review without blind punishment.",
    layerContext: "Contextual",
    layerAuthority: "Authority",
    layerAuthorityText: "Permissions, hierarchy and confirmation are enforced outside the model.",
    layerVerified: "Verified",
    operatorTitle: "Control in natural language",
    operatorText: "From a member to server structure, P.OS turns a request into a verifiable action plan.",
    scopeAria: "Server control mode",
    scopeMembers: "Members",
    scopeStructure: "Structure",
    stepResolve: "Resolve the target",
    stepResolveText: "A member, role or channel is resolved to a Discord object.",
    stepCheck: "Check feasibility",
    stepCheckText: "P.OS verifies permissions, hierarchy and protected targets.",
    stepConfirm: "Confirm the change",
    stepConfirmText: "A critical action never runs on the model's word alone.",
    stepRecord: "Record the result",
    stepRecordText: "Success or refusal remains in the action journal.",
    memoryTitle: "Facts survive a deleted message",
    memoryText: "P.OS keeps a verifiable event journal and separates retrieved data from assumptions.",
    memoryQuery: "Who mentioned my role and then deleted the message?",
    searchAria: "Show or hide matched events",
    eventMention: "Role mention recorded",
    eventMentionText: "Source: server event journal",
    eventDeleted: "Original message deleted",
    eventDeletedText: "The event remains linked to the stored mention record",
    evidenceLabel: "P.OS answer",
    evidenceText: "The fact was found in the journal. Deleting the message did not delete the event record.",
    toolkitTitle: "One interface.\nMany jobs.",
    toolkitText: "P.OS brings daily server tools together, so one request does not need to become a chain of separate commands.",
    toolkitAria: "P.OS capability index",
    toolkitLabel: "P.OS // FUNCTION INDEX",
    toolkitReady: "All systems ready",
    toolDialogue: "Conversation",
    toolDialogueText: "Context, images, video and natural language.",
    toolModeration: "Moderation",
    toolModerationText: "Risk review, anti-raid and justified action.",
    toolControl: "Control",
    toolControlText: "Members, roles, channels, permissions and settings.",
    toolMemory: "Memory",
    toolMemoryText: "Events, mentions, deleted messages and action history.",
    toolForms: "Forms",
    toolFormsText: "Applications, reports and private staff workflows.",
    toolMedia: "Media",
    toolMediaText: "Adaptive GIF creation from images and video.",
    closingTitle: "Not another bot.\nThe operating layer for Discord.",
    closingText: "Created by Pumba, P.OS is evolving as one system for conversation, protection and server control.",
    exploreCta: "See P.OS at work",
    footerText: "Provision Operating System. Created by Pumba.",
    backTop: "Back to top ↑",
  },
};

const consoleModes = {
  ru: {
    overview: {
      command: "system.status",
      title: "P.OS ONLINE",
      summary: "Контекст принят. Система готова к работе.",
      events: [
        ["СЕЙЧАС", "green", "Контекст сервера синхронизирован"],
        ["СЕЙЧАС", "cyan", "Журнал событий доступен"],
        ["СЕЙЧАС", "red", "Операторский контур защищён"],
      ],
    },
    security: {
      command: "security.scan --scope current",
      title: "POLICY ACTIVE",
      summary: "Сигналы проходят через правила, AI-проверку и контур полномочий.",
      events: [
        ["INPUT", "cyan", "Сообщение принято на проверку"],
        ["CHECK", "green", "Контекст не подтверждает угрозу"],
        ["RESULT", "green", "Действие не требуется"],
      ],
    },
    operator: {
      command: "operator.plan --verified",
      title: "ACTION STAGED",
      summary: "Цель определена. До изменения сервер проверит права и подтверждение.",
      events: [
        ["01", "green", "Цель разрешена"],
        ["02", "green", "Иерархия проверена"],
        ["03", "red", "Ожидается подтверждение"],
      ],
    },
    memory: {
      command: "events.search --facts-only",
      title: "FACTS FOUND",
      summary: "Ответ строится из журналов и текущего состояния, а не из догадки.",
      events: [
        ["QUERY", "cyan", "Запрос связан с событиями"],
        ["MATCH", "green", "Источник найден"],
        ["STATE", "red", "Исходное сообщение удалено"],
      ],
    },
  },
  en: {
    overview: {
      command: "system.status",
      title: "P.OS ONLINE",
      summary: "Context received. The system is ready.",
      events: [
        ["NOW", "green", "Server context synchronized"],
        ["NOW", "cyan", "Event journal available"],
        ["NOW", "red", "Operator surface protected"],
      ],
    },
    security: {
      command: "security.scan --scope current",
      title: "POLICY ACTIVE",
      summary: "Signals pass through rules, AI review and an authority gate.",
      events: [
        ["INPUT", "cyan", "Message accepted for review"],
        ["CHECK", "green", "Context does not confirm a threat"],
        ["RESULT", "green", "No action required"],
      ],
    },
    operator: {
      command: "operator.plan --verified",
      title: "ACTION STAGED",
      summary: "The target is resolved. Permissions and confirmation come before change.",
      events: [
        ["01", "green", "Target resolved"],
        ["02", "green", "Hierarchy checked"],
        ["03", "red", "Confirmation pending"],
      ],
    },
    memory: {
      command: "events.search --facts-only",
      title: "FACTS FOUND",
      summary: "The answer is built from journals and current state, never a guess.",
      events: [
        ["QUERY", "cyan", "Request linked to events"],
        ["MATCH", "green", "Source located"],
        ["STATE", "red", "Original message deleted"],
      ],
    },
  },
};

const dialogueExamples = {
  ru: {
    audit: {
      question: "Кто упомянул мою роль, а потом удалил сообщение?",
      answer: "Проверю журнал упоминаний и состояние исходного сообщения. Назову только то, что подтверждается записью события.",
      trace: ["источник: события", "связь: сообщение", "ответ: только факт"],
    },
    role: {
      question: "Выдай роль Moderator пользователю north.",
      answer: "Сначала найду точный логин и роль на сервере, затем проверю иерархию. Изменение потребует подтверждения создателя.",
      trace: ["цель: найти", "иерархия: проверить", "изменение: подтвердить"],
    },
    raid: {
      question: "Проверь, не начался ли рейд после всплеска входов.",
      answer: "Сопоставлю темп входов, возраст аккаунтов и текущий режим защиты. При недостаточных данных не буду объявлять рейд фактом.",
      trace: ["входы: анализ", "контекст: сверить", "решение: обосновать"],
    },
  },
  en: {
    audit: {
      question: "Who mentioned my role and then deleted the message?",
      answer: "I will check the mention journal and the original message state, then report only what the event record verifies.",
      trace: ["source: events", "link: message", "answer: facts only"],
    },
    role: {
      question: "Assign Moderator to the user north.",
      answer: "I will resolve the exact login and role on this server, then check hierarchy. The change requires creator confirmation.",
      trace: ["target: resolve", "hierarchy: verify", "change: confirm"],
    },
    raid: {
      question: "Check whether the recent join spike is a raid.",
      answer: "I will compare join rate, account age and the current protection state. If the evidence is insufficient, I will not call it a raid.",
      trace: ["joins: analyze", "context: compare", "decision: explain"],
    },
  },
};

const operatorCommands = {
  ru: {
    members: "Выдай роль Moderator пользователю north и запиши результат.",
    structure: "Закрой #general для новых сообщений и запиши изменение.",
  },
  en: {
    members: "Assign Moderator to the user north and record the result.",
    structure: "Lock #general for new messages and record the change.",
  },
};

const getInitialLanguage = () => {
  const stored = localStorage.getItem(STORAGE_KEYS.language);
  if (stored === "ru" || stored === "en") return stored;
  const browserLanguage = (navigator.languages?.[0] || navigator.language || "en").toLowerCase();
  return browserLanguage.startsWith("ru") ? "ru" : "en";
};

const getStoredTheme = () => {
  const stored = localStorage.getItem(STORAGE_KEYS.theme);
  if (stored === "light" || stored === "dark") return stored;
  if (stored !== null) localStorage.removeItem(STORAGE_KEYS.theme);
  return null;
};

const resolveTheme = (preference) => preference || (systemTheme.matches ? "light" : "dark");

let currentLanguage = getInitialLanguage();
let currentThemePreference = getStoredTheme();
let activeConsoleMode = "overview";
let activeDialogueExample = "audit";
let activeScope = "members";

const header = document.querySelector("[data-header]");
const nav = document.querySelector("[data-nav]");
const menuToggle = document.querySelector("[data-menu-toggle]");
const metaDescription = document.querySelector('meta[name="description"]');
const ogDescription = document.querySelector('meta[property="og:description"]');
const themeMeta = document.querySelector('meta[name="theme-color"]');

const renderConsole = () => {
  const mode = consoleModes[currentLanguage][activeConsoleMode];
  const command = document.querySelector("[data-console-command]");
  const title = document.querySelector("[data-console-title]");
  const summary = document.querySelector("[data-console-summary]");
  const eventFeed = document.querySelector("[data-console-events]");

  if (command) command.textContent = mode.command;
  if (title) title.textContent = mode.title;
  if (summary) summary.textContent = mode.summary;
  if (eventFeed) {
    eventFeed.replaceChildren();
    mode.events.forEach(([time, state, text]) => {
      const row = document.createElement("p");
      const timestamp = document.createElement("time");
      const status = document.createElement("span");
      const label = document.createElement("span");
      timestamp.textContent = time;
      status.className = `status ${state}`;
      label.textContent = text;
      row.append(timestamp, status, label);
      eventFeed.append(row);
    });
  }

  document.querySelectorAll("[data-console-mode]").forEach((button) => {
    const selected = button.dataset.consoleMode === activeConsoleMode;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
};

const renderDialogue = () => {
  const example = dialogueExamples[currentLanguage][activeDialogueExample];
  const question = document.querySelector("[data-dialogue-question]");
  const answer = document.querySelector("[data-dialogue-answer]");
  const trace = document.querySelector("[data-dialogue-trace]");

  if (question) question.textContent = example.question;
  if (answer) answer.textContent = example.answer;
  if (trace) {
    trace.replaceChildren();
    example.trace.forEach((item) => {
      const marker = document.createElement("span");
      marker.textContent = item;
      trace.append(marker);
    });
  }

  document.querySelectorAll("[data-prompt-example]").forEach((button) => {
    const selected = button.dataset.promptExample === activeDialogueExample;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
};

const renderOperator = () => {
  const command = document.querySelector("[data-operator-command]");
  if (command) command.textContent = operatorCommands[currentLanguage][activeScope];
  document.querySelectorAll("[data-scope]").forEach((button) => {
    const selected = button.dataset.scope === activeScope;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
};

const applyTheme = (preference = currentThemePreference) => {
  currentThemePreference = preference === "light" || preference === "dark" ? preference : null;
  const resolved = resolveTheme(currentThemePreference);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeSource = currentThemePreference ? "user" : "system";
  themeMeta?.setAttribute("content", resolved === "light" ? "#e8ecf1" : "#07080a");

  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === resolved));
  });
};

const applyTranslations = () => {
  const dictionary = translations[currentLanguage];
  document.documentElement.lang = currentLanguage;
  document.documentElement.dataset.lang = currentLanguage;
  document.title = dictionary.pageTitle;
  metaDescription?.setAttribute("content", dictionary.metaDescription);
  ogDescription?.setAttribute("content", dictionary.metaDescription);

  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const key = element.dataset.i18n;
    if (!dictionary[key]) return;
    const value = dictionary[key];
    if (value.includes("\n")) {
      element.replaceChildren();
      value.split("\n").forEach((line, index, lines) => {
        element.append(document.createTextNode(line));
        if (index < lines.length - 1) element.append(document.createElement("br"));
      });
    } else {
      element.textContent = value;
    }
  });

  document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
    const key = element.dataset.i18nAria;
    if (dictionary[key]) element.setAttribute("aria-label", dictionary[key]);
  });

  document.querySelectorAll("[data-i18n-alt]").forEach((element) => {
    const key = element.dataset.i18nAlt;
    if (dictionary[key]) element.setAttribute("alt", dictionary[key]);
  });

  document.querySelectorAll("[data-lang-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.langChoice === currentLanguage));
  });

  renderConsole();
  renderDialogue();
  renderOperator();
};

const updateHeader = () => {
  header?.classList.toggle("is-elevated", window.scrollY > 12);
};

const setMenuOpen = (open) => {
  nav?.classList.toggle("is-open", open);
  menuToggle?.setAttribute("aria-expanded", String(open));
};

const initMenu = () => {
  menuToggle?.addEventListener("click", () => {
    setMenuOpen(menuToggle.getAttribute("aria-expanded") !== "true");
  });

  nav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setMenuOpen(false));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMenuOpen(false);
  });

  document.addEventListener("pointerdown", (event) => {
    if (!header?.contains(event.target)) setMenuOpen(false);
  });
};

const initReveal = () => {
  const items = [...document.querySelectorAll(".reveal")];
  if (reducedMotion.matches || !("IntersectionObserver" in window)) {
    items.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.08, rootMargin: "0px 0px 12% 0px" }
  );

  items.forEach((item, index) => {
    item.style.transitionDelay = `${Math.min(index % 4, 3) * 70}ms`;
    observer.observe(item);
  });
};

const initNavigation = () => {
  const links = [...document.querySelectorAll("[data-nav-link]")];
  const sections = links
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);

  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      links.forEach((link) => {
        link.classList.toggle("is-active", link.getAttribute("href") === `#${visible.target.id}`);
      });
    },
    { rootMargin: "-28% 0px -58% 0px", threshold: [0.01, 0.2, 0.5] }
  );
  sections.forEach((section) => observer.observe(section));
};

const initCursor = () => {
  if (reducedMotion.matches || !finePointer.matches) return;
  const cursor = document.querySelector(".cursor-flow");
  if (!cursor) return;

  const target = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  const current = { ...target };
  let enabled = false;

  window.addEventListener(
    "pointermove",
    (event) => {
      target.x = event.clientX;
      target.y = event.clientY;
      if (!enabled) {
        enabled = true;
        document.documentElement.classList.add("cursor-enabled");
      }
      cursor.style.opacity = "1";
    },
    { passive: true }
  );

  document.addEventListener("pointerleave", () => {
    cursor.style.opacity = "0";
  });

  document.querySelectorAll("a, button, [data-tilt-card]").forEach((element) => {
    element.addEventListener("pointerenter", () => cursor.classList.add("is-hovering"));
    element.addEventListener("pointerleave", () => cursor.classList.remove("is-hovering"));
  });

  const animate = () => {
    current.x += (target.x - current.x) * 0.22;
    current.y += (target.y - current.y) * 0.22;
    cursor.style.transform = `translate3d(${current.x - 17}px, ${current.y - 17}px, 0)`;
    window.requestAnimationFrame(animate);
  };
  animate();
};

const initMagnetic = () => {
  if (reducedMotion.matches || !finePointer.matches) return;
  document.querySelectorAll(".magnetic").forEach((element) => {
    element.addEventListener("pointermove", (event) => {
      const rect = element.getBoundingClientRect();
      const x = (event.clientX - rect.left - rect.width / 2) * 0.06;
      const y = (event.clientY - rect.top - rect.height / 2) * 0.08;
      element.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    });
    element.addEventListener("pointerleave", () => {
      element.style.transform = "";
    });
  });
};

const initTilt = () => {
  if (reducedMotion.matches || !finePointer.matches) return;
  document.querySelectorAll("[data-tilt-card]").forEach((card) => {
    const shell = card.querySelector(".console-shell");
    if (!shell) return;
    card.addEventListener("pointermove", (event) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      shell.style.transform = `rotateY(${-7 + x * 2}deg) rotateX(${2 - y * 1.5}deg) rotateZ(0.5deg)`;
    });
    card.addEventListener("pointerleave", () => {
      shell.style.transform = "";
    });
  });
};

document.querySelectorAll("[data-lang-choice]").forEach((button) => {
  button.addEventListener("click", () => {
    currentLanguage = button.dataset.langChoice;
    localStorage.setItem(STORAGE_KEYS.language, currentLanguage);
    applyTranslations();
  });
});

document.querySelectorAll("[data-theme-choice]").forEach((button) => {
  button.addEventListener("click", () => {
    const preference = button.dataset.themeChoice;
    if (preference !== "dark" && preference !== "light") return;
    localStorage.setItem(STORAGE_KEYS.theme, preference);
    applyTheme(preference);
  });
});

document.querySelectorAll("[data-console-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    activeConsoleMode = button.dataset.consoleMode;
    renderConsole();
  });
});

document.querySelectorAll("[data-prompt-example]").forEach((button) => {
  button.addEventListener("click", () => {
    activeDialogueExample = button.dataset.promptExample;
    renderDialogue();
  });
});

document.querySelectorAll("[data-scope]").forEach((button) => {
  button.addEventListener("click", () => {
    activeScope = button.dataset.scope;
    renderOperator();
  });
});

document.querySelector("[data-evidence-toggle]")?.addEventListener("click", (event) => {
  const result = document.querySelector("[data-evidence-result]");
  const collapsed = result?.classList.toggle("is-collapsed") || false;
  event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
});

systemTheme.addEventListener("change", () => {
  if (currentThemePreference === null) applyTheme();
});

window.addEventListener("scroll", updateHeader, { passive: true });

updateHeader();
applyTheme(currentThemePreference);
applyTranslations();
initMenu();
initReveal();
initNavigation();
initCursor();
initMagnetic();
initTilt();
