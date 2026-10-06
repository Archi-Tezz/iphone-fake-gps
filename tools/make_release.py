"""Build the distributable Windows package, end to end.

Regenerates the icon, freezes the app with PyInstaller, drops a short readme for
the recipient next to the executable and zips the result.

Run: .venv\\Scripts\\python.exe tools/make_release.py
Output: release/ios-loc-<version>-windows.zip
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from iosloc import __version__  # noqa: E402

DIST = ROOT / "dist" / "ios-loc"
RELEASE = ROOT / "release"
ARCHIVE = RELEASE / f"ios-loc-{__version__}-windows.zip"

READER_README = """ios-loc {version} — подмена геопозиции на iPhone по кабелю
================================================================

ЧТО ЭТО
  Ставит iPhone любую геопозицию и может плавно «везти» его по маршруту —
  пешком, на машине, поездом или самолётом. Работает через штатный механизм
  Apple (тот же, что «Simulate Location» в Xcode). Джейлбрейк не нужен.


ПЕРЕД ПЕРВЫМ ЗАПУСКОМ — ОДИН ОБЯЗАТЕЛЬНЫЙ ШАГ
  Windows сам по себе не умеет общаться с iPhone. Нужен драйвер Apple:

      установите «Apple Devices» из Microsoft Store
      (либо iTunes с сайта apple.com — не версию из Store)

  Без этого программа честно скажет, что устройство не найдено,
  и дальше ничего работать не будет.


НА IPHONE (для iOS 16 и новее — то есть почти наверняка)
  Настройки → Конфиденциальность и безопасность → Режим разработчика → включить.
  iPhone перезагрузится и попросит подтвердить.

  ПУНКТА НЕТ В НАСТРОЙКАХ? Так и должно быть до первого обращения с компьютера —
  Apple прячет его, пока iPhone не поговорил с программой разработки.
  Подключите iPhone, запустите ios-loc.exe, нажмите «Подключить» и в открывшемся
  окне — «Не вижу Developer Mode в настройках iPhone». После этого пункт
  появится в Settings → Privacy & Security.


КАК ЗАПУСТИТЬ
  1. Подключите iPhone кабелем и разблокируйте экран.
  2. На телефоне подтвердите «Доверять этому компьютеру».
  3. Запустите ios-loc.exe — откроется панель в браузере.
  4. Нажмите «Подключить», выберите телефон.
  5. Кликните по карте — iPhone окажется там.

  Windows может показать синее окно «Защита Windows». Это потому, что
  программа не подписана сертификатом (он платный), а не потому, что с ней
  что-то не так. Нажмите «Подробнее» → «Выполнить в любом случае».


ЧТО УМЕЕТ ПАНЕЛЬ
  • Клик по карте — переместиться туда.
  • Shift + клик — добавить точку маршрута. Точку можно тянуть мышью,
    клик по ней удаляет. Потом «Начать движение».
  • Правый клик по карте — меню действий.
  • Маркер устройства можно просто перетащить в нужное место.
  • Перетащите GPX-файл в окно — поедет по нему.
  • Профили: на месте, пешком, бег, велосипед, авто, трасса, поезд, самолёт.
    У каждого свой разгон и своё дрожание GPS, чтобы трек выглядел настоящим.
  • W A S D или стрелки — ручное управление, Shift — ускорение.
  • Цифры 1-8 — быстрый выбор профиля, пробел — пауза.
  • Маршрут можно сохранить и загрузить позже, а также выгрузить в GPX.
  • Шестерёнка справа вверху — настройки: язык (русский/английский), тема,
    вид карты, единицы скорости.
    Карт несколько: стандартная, спутник и топографическая работают везде,
    векторные — красивее, но требуют аппаратного ускорения (WebGL). Если
    векторная не отрисуется, программа сама вернётся к стандартной.
  • Стрелка вниз рядом с шестерёнкой прячет чёрное окно: программа продолжает
    работать, значок остаётся в трее у часов (правый клик — меню).
  • Карта следует за точкой; потяните её рукой — слежение отключится, и
    появится кнопка «К устройству», чтобы вернуться обратно.
  • «Вернуть реальную геопозицию» — снять подмену.

БЕЗ КАБЕЛЯ (по желанию)
  Подключите iPhone проводом и один раз выполните в командной строке:

      ios-loc.exe wifi

  После этого кабель можно вынуть — пока телефон и компьютер в одной сети
  Wi-Fi, всё работает так же. По проводу надёжнее: Wi-Fi медленнее и рвётся,
  когда телефон засыпает.

УПРАВЛЕНИЕ С ТЕЛЕФОНА (по желанию)
  Запустите из командной строки:

      ios-loc.exe ui --lan

  Программа покажет QR-код — отсканируйте его телефоном, и панель откроется
  в его браузере. Телефон и компьютер должны быть в одной сети Wi-Fi.
  Кабель всё равно нужен: подмена идёт по нему.

  В этом режиме панель доступна всей вашей локальной сети, поэтому защищена
  кодом доступа — он новый при каждом запуске. В чужой или публичной сети
  этот режим включать не стоит.


ЧТО ВАЖНО ЗНАТЬ
  • Подмена живёт, пока программа запущена. Закрыли окно, выдернули кабель
    или перезагрузили телефон — вернулась настоящая геопозиция. Так устроен
    механизм Apple. Окно можно свернуть в трей, но не закрывать.
  • Шаги и Health не меняются: счётчик шагов берётся с датчиков движения, а не
    из геопозиции. Трекеры, считающие дистанцию по GPS (бег, велосипед),
    работают нормально — километры будут, шаги нет.
  • Приложения видят, что координаты симулированы — iOS их помечает.
    В играх и геосервисах за это обычно блокируют аккаунт.
  • Экстренные вызовы определяют положение иначе, подмена их не затрагивает.
  • Поиск адресов и маршруты по дорогам выключены по умолчанию. Пока галочка
    «Онлайн-сервисы» снята, координаты никуда не отправляются.


ЕСЛИ НЕ РАБОТАЕТ
  Откройте командную строку в этой папке и выполните:

      ios-loc.exe doctor

  Команда проверит драйвер, устройство, Developer Mode и целостность самой
  программы, и скажет, чего не хватает. Её вывод и стоит прислать, если
  что-то не заработало.
"""


#: Files whose absence only surfaces later, as a crash in front of the user.
REQUIRED_IN_BUILD = [
    "_internal/iosloc/static/index.html",
    "_internal/iosloc/static/app.js",
    "_internal/iosloc/static/vendor/maplibre-gl.js",
    "_internal/iosloc/static/vendor/leaflet.js",
    "_internal/iosloc/static/map-styles/dark.json",
    "_internal/iosloc/static/brand/ios-loc.ico",
    # Loaded by ctypes, so no amount of import analysis finds it.
    "_internal/pytun_pmd3/wintun/bin/amd64/wintun.dll",
]


def run(*command: str) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=ROOT, check=True)


def verify(python: str) -> None:
    """Fail the build here rather than on the user's machine.

    Two checks: the files that must be in the bundle, and the imports the
    connection path performs -- run through the frozen executable itself, so a
    missing binary cannot slip through the way wintun.dll once did.
    """
    print("== verify ==")
    missing = [name for name in REQUIRED_IN_BUILD if not (DIST / name).is_file()]
    if missing:
        raise SystemExit("build is incomplete, missing:\n  " + "\n  ".join(missing))
    print(f"  {len(REQUIRED_IN_BUILD)} required files present")

    result = subprocess.run(
        [str(DIST / "ios-loc.exe"), "doctor"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    # Absence of the error marker is not enough: doctor can exit before reaching
    # the checklist (it does when the Apple driver is missing), which is how a
    # missing DLL slipped through once already. Require the checklist to have run.
    if "[ok]" not in output:
        raise SystemExit("doctor did not run the import checklist:\n" + output)
    if "[FAIL]" in output:
        raise SystemExit("the frozen build cannot import what it needs:\n" + output)
    print(f"  frozen build imports everything it needs ({output.count('[ok]')} modules)")


def main() -> None:
    python = sys.executable

    print("== icon ==")
    run(python, "tools/make_icon.py")

    print("== freeze ==")
    # A stale build/ directory makes PyInstaller reuse an old analysis, which is
    # how a changed static file silently fails to reach the package.
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    run(python, "-m", "PyInstaller", "build.spec", "--noconfirm",
        "--distpath", "dist", "--workpath", "build")

    if not (DIST / "ios-loc.exe").is_file():
        raise SystemExit("build failed: ios-loc.exe is missing")

    verify(python)

    print("== readme ==")
    # ASCII filename (some zip tools still mangle Cyrillic entry names), CRLF
    # line endings and a BOM, so it opens correctly in plain Notepad.
    (DIST / "README-ru.txt").write_bytes(
        READER_README.format(version=__version__).replace("\n", "\r\n").encode("utf-8-sig")
    )
    shutil.copy2(ROOT / "iosloc" / "static" / "brand" / "ios-loc.ico", DIST / "ios-loc.ico")

    print("== zip ==")
    RELEASE.mkdir(exist_ok=True)
    ARCHIVE.unlink(missing_ok=True)
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                archive.write(path, Path("ios-loc") / path.relative_to(DIST))

    size_mb = ARCHIVE.stat().st_size / 1024 / 1024
    print(f"\nready: {ARCHIVE}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
