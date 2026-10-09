"""Message translation.

English is the source language: every message in the code is written in English
and looked up here to be translated. That keeps the code readable to anyone and
makes a missing translation degrade to plain English rather than to a key name.

The active language is process-wide. The CLI sets it from the OS locale, the web
UI sets it per request from the panel's language setting, and `set_language`
is the only way either of them does it.
"""

from __future__ import annotations

import locale
import os
from typing import Optional

__all__ = ["RUSSIAN", "ENGLISH", "detect_language", "get_language", "set_language", "t"]

ENGLISH = "en"
RUSSIAN = "ru"
SUPPORTED = (ENGLISH, RUSSIAN)

_current = ENGLISH

#: English source -> Russian. Anything missing falls through as English.
TRANSLATIONS: dict[str, str] = {
    # -- connection ---------------------------------------------------------
    "Apple Mobile Device service (usbmuxd) is unavailable. Install Apple Devices "
    "from the Microsoft Store or iTunes from apple.com, then make sure the Apple "
    "Mobile Device Service is running.":
        "Служба Apple Mobile Device (usbmuxd) недоступна. Установите Apple Devices "
        "из Microsoft Store или iTunes с сайта Apple, затем проверьте, что служба "
        "Apple Mobile Device Service запущена.",
    "No iPhone found. Connect it with a cable, unlock the screen and confirm "
    "\"Trust This Computer\".":
        "iPhone не найден. Подключите кабелем, разблокируйте экран и подтвердите "
        "«Доверять этому компьютеру».",
    "The \"Trust This Computer?\" prompt is open on the iPhone — confirm it and "
    "press Connect again.":
        "На iPhone открыт запрос «Доверять этому компьютеру?» — подтвердите его "
        "и нажмите «Подключить» ещё раз.",
    "You declined to trust this computer on the iPhone.\nUnplug the cable, plug it "
    "back in and choose Trust.":
        "Вы отклонили доверие этому компьютеру на iPhone.\nОтключите кабель, "
        "подключите снова и выберите «Доверять».",
    "The iPhone is locked. Unlock the screen and connect again.":
        "iPhone заблокирован. Разблокируйте экран и повторите подключение.",
    "The iPhone does not trust this computer.\nUnplug the cable, plug it back in, "
    "unlock the screen and tap Trust.":
        "iPhone не доверяет этому компьютеру.\nОтключите кабель, подключите заново, "
        "разблокируйте экран и нажмите «Доверять».",
    "The stored pairing record no longer fits.\nOn the iPhone: Settings → General → "
    "Transfer or Reset iPhone → Reset → Reset Location & Privacy, then connect again.":
        "Старая запись о сопряжении не подходит.\nНа iPhone: Настройки → Основные → "
        "Перенос или сброс iPhone → Сброс → «Сбросить геонастройки и настройки "
        "конфиденциальности», затем подключите заново.",
    "Connect the iPhone by cable first.":
        "Сначала подключите iPhone по кабелю.",
    "No active connection to the device.":
        "Нет активного подключения к устройству.",

    # -- developer mode -----------------------------------------------------
    "Developer Mode is off on the iPhone; Apple will not start developer services "
    "without it.\nEnable it: Settings → Privacy & Security → Developer Mode. The "
    "iPhone will restart.\nIf that entry is missing, press \"I don't see Developer "
    "Mode\" in the device list to reveal it.":
        "На iPhone выключен Developer Mode, без него Apple не даёт запускать сервисы "
        "разработчика.\nВключите: Настройки → Конфиденциальность и безопасность → "
        "Режим разработчика. iPhone перезагрузится.\nЕсли пункта нет — нажмите "
        "«Не вижу Developer Mode» в списке устройств, чтобы он появился.",
    "Developer Mode is off, so the device refused to mount the developer image.":
        "Устройство отказало в монтировании образа разработчика: выключен Developer Mode.",
    "To enable Developer Mode automatically the iPhone must have no passcode set, "
    "or you can enable it by hand in Settings.":
        "Чтобы включить Developer Mode автоматически, с iPhone нужно временно снять "
        "код-пароль, либо включите режим вручную в Настройках.",
    "Developer Mode is being enabled — the iPhone is restarting. Confirm it on the "
    "device after it boots, then connect again.":
        "Developer Mode включается — iPhone перезагружается. После загрузки подтвердите "
        "включение на экране и подключитесь снова.",
    "Done. On the iPhone open:\n    Settings → Privacy & Security → Developer Mode\n"
    "Turn the switch on — the iPhone will restart and ask you to confirm.":
        "Готово. Теперь на iPhone откройте:\n    Настройки → Конфиденциальность и "
        "безопасность → Режим разработчика\nВключите переключатель — iPhone "
        "перезагрузится и попросит подтвердить.",
    "Developer Mode is already on — you can connect.":
        "Developer Mode уже включён — можно подключаться.",

    # -- device state -------------------------------------------------------
    "(unknown)": "(неизвестно)",
    "Could not query the device: {error}": "Не удалось опросить устройство: {error}",
    "Developer Mode is off. On the iPhone: Settings → Privacy & Security → "
    "Developer Mode → turn on (the iPhone restarts).":
        "Developer Mode выключен. На iPhone: Настройки → Конфиденциальность и "
        "безопасность → Режим разработчика → включить (iPhone перезагрузится).",

    # -- mounting and tunnel -------------------------------------------------
    "The iPhone does not have enough free space for the developer image.":
        "На iPhone не хватает свободного места для образа разработчика.",
    "Could not mount the Developer Disk Image: {error}\nUsually fixed by unlocking "
    "the iPhone, keeping the cable in, and checking your internet connection (the "
    "image is downloaded from Apple).\nIf the iOS version is very new, update the "
    "library: pip install -U pymobiledevice3":
        "Не удалось смонтировать образ разработчика (DDI): {error}\nЧаще всего "
        "помогает: разблокировать iPhone, не отключать кабель, проверить интернет "
        "(образ скачивается с серверов Apple).\nЕсли на iPhone совсем свежая iOS — "
        "обновите библиотеку: pip install -U pymobiledevice3",
    "Could not establish the tunnel to the device (iOS {version}): {error}\nIf the "
    "iOS version is newer than the library, update it first:\n    pip install -U "
    "pymobiledevice3\nWorkaround: run `pymobiledevice3 remote tunneld` in a separate "
    "window as administrator and try again.":
        "Не удалось поднять туннель до устройства (iOS {version}): {error}\nЕсли iOS "
        "новее самой программы, сначала обновите библиотеку:\n    pip install -U "
        "pymobiledevice3\nВариант обхода: запустите в отдельном окне с правами "
        "администратора\n    pymobiledevice3 remote tunneld\nи повторите попытку.",
    "Could not open the location channel: {error}\nCheck that the iPhone is "
    "unlocked, Developer Mode is on and the developer image is mounted.":
        "Не удалось открыть канал подмены локации: {error}\nПроверьте, что iPhone "
        "разблокирован, Developer Mode включён, а образ разработчика смонтирован.",
    "The location channel dropped: {error}":
        "Канал подмены локации оборвался: {error}",
    "Lost the device and could not get it back. Check the cable and connect again.":
        "Устройство пропало и не вернулось. Проверьте кабель и подключитесь заново.",

    # -- wireless ------------------------------------------------------------
    "A cable is required: wireless access can only be enabled over USB.\nError: {error}":
        "Нужен кабель: включить работу по Wi-Fi можно только при проводном "
        "подключении.\nОшибка: {error}",
    "Could not change the Wi-Fi connection setting: {error}":
        "Не удалось переключить подключение по Wi-Fi: {error}",
    "Wi-Fi access is off — cable only from now on.":
        "Подключение по Wi-Fi выключено — теперь только по кабелю.",
    "Done. The iPhone is now reachable without a cable while it is on the same "
    "Wi-Fi network.\nUnplug the cable and press Refresh in the device list — it "
    "should appear marked Network.":
        "Готово. Теперь iPhone виден и без кабеля, пока он в той же сети Wi-Fi.\n"
        "Отключите кабель и нажмите «Обновить» в списке устройств — устройство "
        "должно появиться с пометкой Network.",
    "On iOS {version} Developer Mode is not needed — you can connect right away.":
        "На iOS {version} режим разработчика не нужен — можно сразу подключаться.",
    "Could not reveal the Developer Mode entry: {error}\nCheck that the iPhone is "
    "unlocked and trusts this computer.":
        "Не удалось показать пункт Developer Mode: {error}\nПроверьте, что iPhone "
        "разблокирован и доверяет этому компьютеру.",
    "Could not connect to the iPhone: {error}\nConnect it by cable, unlock the "
    "screen and confirm \"Trust This Computer\".":
        "Не удалось подключиться к iPhone: {error}\nПодключите кабелем, разблокируйте "
        "экран и подтвердите «Доверять этому компьютеру».",

    # -- session -------------------------------------------------------------
    "Set a position first.": "Сначала задайте точку или маршрут.",
    "A route needs at least one point": "маршруту нужна хотя бы одна точка",
    "The starting point is unknown — place a position on the map first.":
        "Неизвестна стартовая точка — сначала поставьте позицию на карте.",

    # -- server --------------------------------------------------------------
    "Unexpected error: {error}\nThe full text is in the program window (the black "
    "console).":
        "Непредвиденная ошибка: {error}\nПолный текст — в окне программы (чёрное "
        "консольное окно).",
    "Address search sends the query to OpenStreetMap. Enable \"Online services\" "
    "in settings if that is acceptable.":
        "Поиск по адресу отправляет запрос в OpenStreetMap. Включите «Онлайн-сервисы» "
        "в настройках, если это приемлемо.",
    "Road routing runs on the public OSRM server. Enable \"Online services\" in "
    "settings if that is acceptable.":
        "Маршрут по дорогам считается на публичном сервере OSRM. Включите "
        "«Онлайн-сервисы» в настройках, если это приемлемо.",
    "Search is unavailable: {error}": "Поиск недоступен: {error}",
    "The router is unavailable: {error}": "Маршрутизатор недоступен: {error}",
    "No road route found": "Маршрут по дорогам не найден",
    "mode must be driving, walking or cycling":
        "режим должен быть driving, walking или cycling",
    "Phone access is off": "Доступ с телефона выключен",
    "Phone access is off. Start the program with --lan (ios-loc.exe ui --lan) to "
    "open the panel on your Wi-Fi network.":
        "Доступ с телефона выключен. Запустите программу с ключом --lan "
        "(ios-loc.exe ui --lan), чтобы открыть панель в своей Wi-Fi-сети.",
    "Could not determine the local network address":
        "Не удалось определить адрес в локальной сети",
    "Could not build the QR code": "Не удалось построить QR-код",
    "An access code is required. Open the link with the code, or scan the QR.":
        "Нужен код доступа. Откройте ссылку с кодом или отсканируйте QR.",
}


def detect_language() -> str:
    """Pick a language from the environment, defaulting to English."""
    for value in (os.environ.get("IOSLOC_LANG"), os.environ.get("LANG")):
        if value and value[:2].lower() in SUPPORTED:
            return value[:2].lower()
    try:
        system = locale.getlocale()[0] or ""
    except ValueError:
        system = ""
    if system[:2].lower() == RUSSIAN or "Russian" in system:
        return RUSSIAN
    return ENGLISH


def set_language(language: Optional[str]) -> str:
    global _current
    if language and language.lower() in SUPPORTED:
        _current = language.lower()
    return _current


def get_language() -> str:
    return _current


def t(message: str, language: Optional[str] = None, **kwargs) -> str:
    """Translate a message and fill in its placeholders.

    An untranslated message is returned as written, so adding a string never
    breaks a build -- it just stays English until someone translates it.
    """
    chosen = (language or _current).lower()
    text = TRANSLATIONS.get(message, message) if chosen == RUSSIAN else message
    return text.format(**kwargs) if kwargs else text
