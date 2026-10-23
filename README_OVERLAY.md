# 🕹️ PownOverlay — внутриигровой оверлей для Pownlauncher

Оверлей рисуется **внутри процесса Minecraft** (javaw.exe) через Dear ImGui,
управляется кнопками прямо из игры: Браузер / Калькулятор / Блокнот / Таймер.

```
[Python Launcher] --(inject DLL)--> [javaw.exe (Minecraft)]
                                          |
                              hook wglSwapBuffers → ImGui Render
                              hook WndProc        → ImGui Input
```

---

## 1. Python-сторона (уже вшита в лаунчер)

### Файлы
- `overlay_injector.py` — модуль инъекции (два метода):
  1. **injectpy** (предпочтительный),
  2. встроенный fallback **CreateRemoteThread + LoadLibraryW** (ctypes, без зависимостей).
- `clodmain.py` — `LauncherEngine._launch()` после `subprocess.Popen` вызывает
  `maybe_inject_overlay()`: фоновый поток ждёт ~10 сек (инициализация OpenGL-контекста JVM),
  затем инжектит DLL по PID запущенного процесса. Ошибки инъекции **не ломают запуск игры** —
  они только пишутся в лог (`plauncher.log`, префикс `[Overlay]`).

### Установка injectpy (опционально, но рекомендуется)
```bash
git clone https://github.com/Florararie/injectpy.git
cd injectpy
pip install .
```
Если injectpy не установлен — сработает fallback-метод на ctypes.

### Где лежит DLL
Лаунчер ищет её в порядке:
1. `<папка лаунчера>/overlay/PownOverlay.dll`
2. `<папка лаунчера>/PownOverlay.dll`
3. внутри PyInstaller-архива (`sys._MEIPASS/overlay/PownOverlay.dll`)

### Как включить
**Профиль → чекбокс «Инжектить PownOverlay.dll после запуска игры»** (виден только на Windows) → Сохранить.
Настройка хранится в `profile.json` как `"overlay": true`.

Важные детали реализации:
- Инъекция идёт **по PID конкретного процесса**, а не по имени `javaw.exe` — это
  исключает попадание в чужие Java-процессы (Advantage, Eclipse и т.п.).
- Sentinel-файл `custom/<сборка>/.overlay_injected` предотвращает повторную инъекцию;
  удалите его, чтобы инжектить заново.
- Если игра закрылась раньше истечения паузы — инъекция автоматически отменяется.

---

## 2. C++ сторона: сборка PownOverlay.dll

### Требования
- Visual Studio 2019+ (Desktop development with C++), сборка **x64 Release**.
- [MinHook](https://github.com/TsudaKageyu/minhook) → распакуйте в `overlay/deps/minhook/`
- [Dear ImGui](https://github.com/ocornut/imgui) → распакуйте в `overlay/deps/imgui/`
  (backend'и `imgui_impl_win32.*` и `imgui_impl_opengl3.*` лежат в `deps/imgui/backends/`)

### Сборка
Из **«x64 Native Tools Command Prompt for VS»**:
```bat
cd overlay
build_dll.bat
```
Получившийся `PownOverlay.dll` положите рядом с лаунчером в папку `overlay/`.

### Что делает DLL (`overlay/PownOverlay.cpp`)
| Компонент | Реализация |
|---|---|
| Перехват рендера | MinHook на `wglSwapBuffers` (opengl32.dll уже загружена игрой) |
| Инициализация ImGui | при первом свопе буфера: `WindowFromDC(hDC)` → hwnd игры, `ImGui_ImplWin32_Init` + `ImGui_ImplOpenGL3_Init("#version 150")` |
| Ввод (клики кнопок) | подмена `WndProc` → `ImGui_ImplWin32_WndProcHandler`; оригинал сохраняется **до** подмены, нерасходившиеся сообщения идут игре через `CallWindowProc` |
| Кнопки | `ShellExecuteA`: `https://www.google.com`, `calc.exe`, `notepad.exe`, `ms-clock:` (приложение «Часы», вкладка «Таймер») |
| Хоткей | **F2** — скрыть/показать оверлей; при скрытии фокус возвращается окну игры (персонаж снова слушает WASD) |
| Выгрузка | `DLL_PROCESS_DETACH` снимает WndProc и хуки (Cleanup) |

---

## 3. Производительность и безопасность

- Хук держится коротким: тяжёлой работы, файловых операций и блокирующих вызовов
  в `Hooked_wglSwapBuffers` нет — только NewFrame → Render → оригинальный swap.
- Просадки FPS обычно дают не сами хуки, а конфликты с другими оверлеями
  (Discord, MSI Afterburner, OBS) — при тестах отключайте их.
- Инъекция DLL — техника, которую антивирусы считают подозрительной.
  **Единственный правильный путь — прозрачность**: открытый исходный код
  и инструкция для пользователя:
  > *Windows Security → Virus & threat protection → Manage settings → Exclusions →
  > Add an exclusion → выберите папку лаунчера (Plauncher.exe и overlay\PownOverlay.dll).*
- Не используйте обходы/маскировки от антивирусов и поддельные сертификаты.

---

## 4. Чек-лист тестирования

1. Установлен ли injectpy? (нет → должен сработать ctypes-fallback, проверьте лог)
2. В профиле включён чекбокс оверлея, DLL лежит в `overlay/PownOverlay.dll`.
3. Запустить игру → подождать 10 c → в окне Minecraft появляется окно «Pown Tools».
4. Кнопки кликаются (значит WndProc-хук живой); после закрытия оверлея по F2
   управление игрой полностью возвращается.
5. Игра не вылетает при выходе (Cleanup снимает хук и WndProc).

### Частые проблемы
| Симптом | Причина / решение |
|---|---|
| ImGui не реагирует на мышь | не подменён WndProc или `o_WndProc` сохранён после подмены → пересобрать DLL (см. раздел 2) |
| DLL не инжектится | разрядность: лаунчер обязан быть x64, иначе CreateRemoteThread в 64-битный процесс не сработает; либо права — запуск от админа |
| LoadLibraryW вернул NULL | DLL собрана не x64 или не хватает CRT-зависимостей (используйте `/MT`, как в build_dll.bat) |
| Оверлей не появляется | injection слишком ранняя: увеличьте `"overlay_wait"` в profile.json (секунды, 5–60) |
| Антивирус удаляет файл | добавьте исключения (раздел 3) |
