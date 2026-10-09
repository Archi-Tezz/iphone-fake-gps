/* Interface translations.
 *
 * English is the source language, same as on the server: every key here is the
 * English string itself, so an untranslated phrase degrades to readable English
 * rather than to a key name.
 *
 * Markup carries `data-i18n` on text nodes and `data-i18n-attr="attr:key"` for
 * attributes (title, placeholder); `applyTranslations()` walks both.
 */

export const LANGUAGES = ["en", "ru"];

const RU = {
  // -- header ---------------------------------------------------------------
  "No device connected": "Устройство не подключено",
  "Connect": "Подключить",
  "Disconnect": "Отключить",
  "Connecting…": "Подключаем…",
  "Open on phone": "Открыть на телефоне",
  "Minimise to tray": "Свернуть в трей",
  "Settings": "Настройки",
  "Panel": "Панель",

  // -- search ---------------------------------------------------------------
  "Coordinates or address": "Координаты или адрес",
  "Search": "Найти",
  "Searching…": "Ищем…",
  "Nothing found": "Ничего не найдено",

  // -- profiles -------------------------------------------------------------
  "Movement profile": "Профиль движения",
  "Stationary": "На месте",
  "Standing still, only natural GPS drift": "Стоим на точке, только естественный дрейф GPS",
  "Walking": "Пешком",
  "5 km/h, an unhurried pace": "5 км/ч — спокойный шаг",
  "Running": "Бег",
  "12 km/h, an easy jog": "12 км/ч — лёгкий бег",
  "Cycling": "Велосипед",
  "20 km/h": "20 км/ч",
  "Car, city": "Авто, город",
  "50 km/h": "50 км/ч",
  "Car, highway": "Авто, трасса",
  "90 km/h": "90 км/ч",
  "Train": "Поезд",
  "160 km/h, accelerates like a train": "160 км/ч, разгон как у состава",
  "Plane": "Самолёт",
  "900 km/h, long climb and gentle turns": "900 км/ч, долгий разгон и плавные развороты",
  "Speed": "Скорость",
  "One way": "В одну сторону",
  "Back and forth": "Туда-обратно",
  "Loop": "По кругу",

  // -- route ----------------------------------------------------------------
  "Route": "Маршрут",
  "no points": "точек нет",
  "1 point": "1 точка",
  "Undo point": "Отменить точку",
  "Clear": "Очистить",
  "Snap to roads": "По дорогам",
  "Build a road route (online)": "Проложить маршрут по дорогам (онлайн)",
  "Save route": "Сохранить маршрут",
  "Export GPX": "Выгрузить GPX",
  "Load GPX": "Загрузить GPX",
  "Repeat last": "Повторить последний",
  "Start moving": "Начать движение",

  // -- manual ---------------------------------------------------------------
  "Manual control": "Ручное управление",
  "Forward": "Вперёд",
  "Back": "Назад",
  "Left": "Влево",
  "Right": "Вправо",
  "Stop": "Стоп",
  "Pause": "Пауза",
  "Resume": "Продолжить",
  "Restore real location": "Вернуть реальную геопозицию",

  // -- saved ----------------------------------------------------------------
  "Saved places": "Сохранённые точки",
  "Save current place": "Сохранить текущую точку",
  "Saved routes": "Сохранённые маршруты",
  "Empty for now": "Пока пусто",
  "Delete": "Удалить",

  // -- hud ------------------------------------------------------------------
  "Position": "Координаты",
  "Mode": "Режим",
  "Remaining": "Осталось",
  "Back to device": "К устройству",
  "idle": "ожидание",
  "route": "маршрут",
  "manual": "ручное",
  "holding": "удержание",
  "paused": "пауза",

  // -- map menu -------------------------------------------------------------
  "Move here": "Переместиться сюда",
  "Add to route": "Добавить в маршрут",
  "Save as place": "Сохранить как точку",
  "Copy coordinates": "Скопировать координаты",

  // -- devices --------------------------------------------------------------
  "Devices over USB": "Устройства",
  "Querying USB…": "Опрашиваем устройства…",
  "Enable Developer Mode automatically": "Включить Developer Mode автоматически",
  "The iPhone will restart": "iPhone перезагрузится",
  "I don't see \"Developer Mode\" in iPhone settings":
    "Не вижу «Developer Mode» в настройках iPhone",
  "Refresh": "Обновить",
  "Close": "Закрыть",
  "Nothing found. Connect an iPhone by cable, unlock the screen and confirm \"Trust This Computer\".":
    "Ничего не найдено. Подключите iPhone кабелем, разблокируйте экран и подтвердите «Доверять этому компьютеру».",

  // -- settings -------------------------------------------------------------
  "Interface language": "Язык интерфейса",
  "System": "Как в системе",
  "Appearance": "Тема оформления",
  "Light": "Светлая",
  "Dark": "Тёмная",
  "Map": "Вид карты",
  "Auto": "Авто",
  "Standard": "Стандартная",
  "Satellite": "Спутник",
  "Topographic": "Топографическая",
  "Vector": "Векторная",
  "Vector light": "Векторная светлая",
  "Vector dark": "Векторная тёмная",
  "The first three work everywhere. Vector maps look better but need hardware acceleration (WebGL); without it the program falls back to Standard by itself.":
    "Первые три работают везде. Векторные — красивее и чётче, но требуют аппаратного ускорения (WebGL); если его нет, программа сама вернётся к стандартной.",
  "Speed units": "Единицы скорости",
  "km/h": "км/ч",
  "mph": "миль/ч",
  "m/s": "м/с",
  "Follow with camera": "Следить камерой",
  "The map moves with the point": "Карта едет за точкой",
  "Show travelled path": "Показывать пройденный путь",
  "A green trail on the map": "Зелёный след на карте",
  "Keyboard control": "Управление с клавиатуры",
  "W A S D and arrow keys": "W A S D и стрелки",
  "Tilt and rotate": "Наклон и поворот карты",
  "Right mouse button — 3D view": "Правая кнопка мыши — 3D-вид",
  "Online services": "Онлайн-сервисы",
  "Address search and road routing via OpenStreetMap and OSRM. Off means no coordinates leave this machine.":
    "Поиск адресов и маршруты по дорогам через OpenStreetMap и OSRM. Выключено — координаты никуда не уходят.",
  "Reset": "Сбросить",
  "Done": "Готово",
  "Settings reset.": "Настройки сброшены.",

  // -- phone ----------------------------------------------------------------
  "Scan the code with your phone, or open the link in its browser.":
    "Отсканируйте код телефоном или откройте ссылку в его браузере.",
  "QR code for the phone": "QR-код для телефона",
  "Other addresses of this computer:": "Другие адреса этого компьютера:",
  "The access code lasts while the program runs. The phone must be on the same Wi-Fi; the cable is still needed, the override goes through it.":
    "Код доступа действует, пока программа запущена. Телефон должен быть в той же сети Wi-Fi; кабель всё равно нужен — подмена идёт по нему.",
  "The phone must be on the same Wi-Fi as the computer. The cable is still needed — the override goes through it.":
    "Телефон должен быть в той же сети Wi-Fi, что и компьютер. Кабель при этом всё равно нужен — именно по нему идёт подмена.",
  "Checking…": "Проверяем…",

  // -- journey --------------------------------------------------------------
  "Journey": "Путешествие",
  "Plan": "Проложить",
  "Planning…": "Прокладываем…",
  "Destination: city, airport or coordinates": "Куда: город, аэропорт или координаты",
  "Where do you want to end up? A city, an airport code or coordinates. The route is planned for you: drive to the airport, fly, then continue on the ground.":
    "Куда хотите попасть? Город, код аэропорта или координаты. Маршрут проложится сам: доехать до аэропорта, перелёт, дальше по земле.",
  "Start the journey": "Отправиться",
  "Journey name:": "Название путешествия:",
  "Journey saved as \"{name}\".": "Путешествие «{name}» сохранено.",
  "Loaded \"{name}\". Press Start the journey.": "Загружено «{name}». Нажмите «Отправиться».",
  "Total: {distance}": "Всего: {distance}",
  "leg {n} of {total}": "этап {n} из {total}",
  "Set a starting position on the map first.": "Сначала поставьте начальную точку на карте.",
  "Ground route": "По земле",
  "Drive to {code}": "Доехать до {code}",
  "Fly {from} → {to}": "Перелёт {from} → {to}",
  "Drive from {code}": "Доехать от {code}",
  "Walk from {code}": "Пешком от {code}",

  // -- hints ----------------------------------------------------------------
  "<b>Shift + click</b> on the map adds a route point.<br>Right-click for actions. Drag a point to move it, click it to delete.":
    "<b>Shift + клик</b> по карте — добавить точку маршрута.<br>Правый клик по карте — меню действий. Точку можно тянуть мышью, клик по ней — удалить.",
  "Hold W A S D or the arrow keys to move. Shift speeds up.<br>Digits 1–8 pick a profile, space pauses.":
    "Клавиши W A S D или стрелки — пока нажаты, идём. Shift — ускорение.<br>Цифры 1–8 — профиль движения, пробел — пауза.",
  "Drag to move · click to delete": "Потяните, чтобы сдвинуть · клик — удалить",

  // -- toasts ---------------------------------------------------------------
  "Connecting to {name}…": "Подключаемся к {name}…",
  "Connected. Click the map to set a position.":
    "Подключено. Кликните по карте, чтобы поставить точку.",
  "Disconnected, real location restored": "Отключено, реальная геопозиция возвращена",
  "Real location restored": "Реальная геопозиция возвращена",
  "Place name:": "Название точки:",
  "Route name:": "Название маршрута:",
  "Route": "Маршрут",
  "Need at least two route points first.": "Сначала наметьте маршрут хотя бы из двух точек.",
  "Need at least two route points.": "Нужно хотя бы две точки маршрута.",
  "No route has been started yet.": "Ещё не было ни одного запущенного маршрута.",
  "Route saved as \"{name}\".": "Маршрут «{name}» сохранён.",
  "Place saved as \"{name}\".": "Точка «{name}» сохранена.",
  "Loaded \"{name}\". Press Start moving.": "Загружен «{name}». Нажмите «Начать движение».",
  "Route exported to GPX.": "Маршрут сохранён в GPX.",
  "Loaded {count} points. Press Start moving.":
    "Загружено точек: {count}. Нажмите «Начать движение».",
  "Road route: {distance}": "Маршрут по дорогам: {distance}",
  "Copied: {text}": "Скопировано: {text}",
  "That is not a pair of coordinates. Enable Online services in settings to search by address.":
    "Это не координаты. Для поиска по адресу включите «Онлайн-сервисы» в настройках.",
  "Road routing runs on the public OSRM server. Enable Online services in settings.":
    "Маршрут по дорогам считается на публичном сервере OSRM. Включите «Онлайн-сервисы» в настройках.",
  "Window minimised. The program keeps running — its icon is in the tray, by the clock.":
    "Окно свёрнуто. Программа продолжает работать — значок в трее, у часов.",
  "Nothing to minimise: there is no console window. The tray icon is still there.":
    "Сворачивать нечего: окна консоли нет. Значок в трее всё равно доступен.",
  "Map tiles are not loading — looks like there is no internet. Coordinates, routes and GPX work as usual.":
    "Тайлы карты не загружаются — похоже, нет доступа в интернет. Координаты, маршруты и GPX работают как обычно.",
  "The vector map did not start ({error}). Switching to Standard — you can pick it again in settings.":
    "Векторная карта не запустилась ({error}). Переключаюсь на стандартную — её можно выбрать заново в настройках.",
  "Map \"{name}\" did not start: {error}": "Карта «{name}» не запустилась: {error}",
  "Could not show the map: {error}": "Не удалось показать карту: {error}",
  "On the way: {distance} at {speed}": "В путь: {distance} на {speed}",
  ", about {time}": ", примерно {time}",
  "GPX: {error}": "GPX: {error}",
  "the file does not parse as GPX": "файл не читается как GPX",
  "no points in the file": "в файле нет точек",
  "Could not read the file": "Не удалось прочитать файл",
  "Could not load profiles: {error}": "Не удалось получить профили: {error}",
  "The map did not render. Usually that means no internet, or hardware acceleration (WebGL) is disabled in the browser. Everything else works: coordinates, routes, GPX and keyboard control.":
    "Карта не отрисовалась. Обычно это значит, что нет интернета или в браузере отключено аппаратное ускорение (WebGL). Всё остальное работает: координаты, маршруты, GPX и управление с клавиатуры.",

  // -- units and time -------------------------------------------------------
  "{n} s": "{n} с",
  "{n} min": "{n} мин",
  "{h} h {m} min": "{h} ч {m} мин",
  "{n} m": "{n} м",
  "{n} km": "{n} км",
  "{n} points": "{n} точек",
};

const DICTIONARIES = { ru: RU };

let current = "en";

export function detectLanguage() {
  const stored = (navigator.language || "en").slice(0, 2).toLowerCase();
  return LANGUAGES.includes(stored) ? stored : "en";
}

export function setLanguage(language) {
  current = LANGUAGES.includes(language) ? language : "en";
  document.documentElement.lang = current;
  return current;
}

export function getLanguage() {
  return current;
}

/** Translate `text`, substituting {placeholders} from `values`. */
export function t(text, values) {
  const dictionary = DICTIONARIES[current];
  let result = (dictionary && dictionary[text]) || text;
  if (values) {
    for (const [key, value] of Object.entries(values)) {
      result = result.replaceAll(`{${key}}`, String(value));
    }
  }
  return result;
}

/** Re-translate every marked node in the document. */
export function applyTranslations(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    const key = node.dataset.i18n;
    const translated = t(key);
    // Some strings carry inline markup (<b>, <br>) on purpose.
    if (/[<>]/.test(translated)) node.innerHTML = translated;
    else node.textContent = translated;
  }
  for (const node of root.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of node.dataset.i18nAttr.split(";")) {
      const [attr, key] = pair.split(":");
      if (attr && key) node.setAttribute(attr.trim(), t(key.trim()));
    }
  }
}
