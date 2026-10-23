@echo off
REM ============================================================
REM  Сборка PownOverlay.dll (x64 Release) через cl.exe
REM  Запускать из "x64 Native Tools Command Prompt for VS 2019/2022"
REM  или предварительно вызвав vcvarsall.bat x64.
REM
REM  Ожидаемая структура deps:
REM    overlay\deps\minhook\   (github.com/TsudaKageyu/minhook)
REM        include\MinHook.h, src\hook.c, src\buffer.c, src\trampoline.c, src\hde\*.c
REM    overlay\deps\imgui\     (github.com/ocornut/imgui)
REM        *.h *.cpp + backends\imgui_impl_win32.* + backends\imgui_impl_opengl3.*
REM ============================================================
cd /d "%~dp0"

if not exist build mkdir build

cl /nologo /LD /O2 /EHsc /MT ^
   /Fo"build\\" ^
   /D "WIN32" /D "NDEBUG" /D "_WINDOWS" /D "_USRDLL" ^
   /I "deps\minhook\include" /I "deps\imgui" /I "deps\imgui\backends" ^
   PownOverlay.cpp ^
   "deps\minhook\src\hook.c" "deps\minhook\src\buffer.c" "deps\minhook\src\trampoline.c" ^
   "deps\minhook\src\hde\hde32.c" "deps\minhook\src\hde\hde64.c" ^
   "deps\imgui\imgui.cpp" "deps\imgui\imgui_draw.cpp" "deps\imgui\imgui_tables.cpp" ^
   "deps\imgui\imgui_widgets.cpp" ^
   "deps\imgui\backends\imgui_impl_win32.cpp" "deps\imgui\backends\imgui_impl_opengl3.cpp" ^
   /link /OUT:PownOverlay.dll ^
   opengl32.lib user32.lib gdi32.lib shell32.lib dwmapi.lib

if exist PownOverlay.dll (
    echo.
    echo [OK] PownOverlay.dll собрана.
    echo Положите её в ^<папка лаунчера^>\overlay\PownOverlay.dll
    exit /b 0
) else (
    echo.
    echo [FAIL] Сборка не удалась. Проверьте deps\ и запуск из x64 Native Tools Prompt.
    exit /b 1
)
