// ============================================================
//  PownOverlay.dll — внутриигровой оверлей для Minecraft Java
//  Хук wglSwapBuffers (MinHook) + Dear ImGui (win32/opengl32).
//  F2 — показать/скрыть оверлей и вернуть фокус игре.
//  Сборка: x64 Release, папка deps/ с MinHook и ImGui.
// ============================================================

#include <Windows.h>
#include <shellapi.h>
#include <MinHook.h>
#include <imgui.h>
#include <imgui_impl_win32.h>
#include <imgui_impl_opengl3.h>
#include <GL/gl.h>

// ---------- состояние ----------
typedef BOOL(WINAPI* wglSwapBuffers_t)(HDC);
wglSwapBuffers_t o_wglSwapBuffers = nullptr;

bool    g_ImGuiInitialized = false;
HWND    g_GameHwnd = nullptr;
WNDPROC o_WndProc = nullptr;
volatile bool g_OverlayVisible = true;   // F2 — тумблер
HMODULE g_Module = nullptr;

extern void RenderOverlayUI();
extern LRESULT CALLBACK HookedWndProc(HWND, UINT, WPARAM, LPARAM);

// ---------- хоткей F2 (отдельный поток, не зависит от WndProc) ----------
DWORD WINAPI HotkeyThread(LPVOID) {
    bool wasDown = false;
    while (true) {
        SHORT down = (GetAsyncKeyState(VK_F2) & 0x8000) != 0;
        if (down && !wasDown) {
            g_OverlayVisible = !g_OverlayVisible;
            // Вернуть фокус окну игры, чтобы персонаж снова реагировал на WASD
            if (g_GameHwnd && !g_OverlayVisible)
                SetForegroundWindow(g_GameHwnd);
        }
        wasDown = down;
        Sleep(50);
    }
    return 0;
}

// ---------- хук wglSwapBuffers ----------
BOOL WINAPI Hooked_wglSwapBuffers(HDC hDC) {
    // 1. Инициализация ImGui при первом свопе (OpenGL-контекст уже активен)
    if (!g_ImGuiInitialized) {
        g_GameHwnd = WindowFromDC(hDC);
        if (g_GameHwnd) {
            IMGUI_CHECKVERSION();
            ImGui::CreateContext();
            ImGuiIO& io = ImGui::GetIO();
            io.IniFilename = nullptr;                 // не создавать imgui.ini
            io.ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;

            ImGui_ImplWin32_Init(g_GameHwnd);
            ImGui_ImplOpenGL3_Init("#version 150");   // MC использует GL >= 3.2, 150 безопасен

            // Подмена WndProc для обработки ввода (сохраняем оригинал ДО подмены!)
            o_WndProc = (WNDPROC)SetWindowLongPtr(g_GameHwnd, GWLP_WNDPROC,
                                                  (LONG_PTR)HookedWndProc);
            g_ImGuiInitialized = true;
        }
    }

    // 2. Отрисовка кадра ImGui (короткий путь, когда скрыто — ничего не рисуем)
    if (g_ImGuiInitialized && g_OverlayVisible) {
        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        RenderOverlayUI();

        ImGui::Render();
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
    } else if (g_ImGuiInitialized) {
        // Кадр всё равно нужно «прогнать», чтобы ImGui корректно обрабатывал dt
        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        ImGui::Render();
    }

    // 3. Обязательный вызов оригинала. Держим функцию максимально короткой,
    //    иначе просадим FPS игры (см. предупреждение goldberg_emulator).
    return o_wglSwapBuffers(hDC);
}

// ---------- WndProc: ввод для ImGui ----------
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg,
                                                             WPARAM wParam, LPARAM lParam);

LRESULT CALLBACK HookedWndProc(HWND hWnd, UINT uMsg, WPARAM wParam, LPARAM lParam) {
    if (g_ImGuiInitialized && g_OverlayVisible &&
        ImGui_ImplWin32_WndProcHandler(hWnd, uMsg, wParam, lParam)) {
        // Клик по оверлею: забираем мышь у игры только пока курсор над окном ImGui.
        // Игра продолжит получать клавиши, т.к. ImGui "съедает" только сообщения ввода.
        return true;
    }
    return CallWindowProc(o_WndProc, hWnd, uMsg, wParam, lParam);
}

// ---------- UI: кнопки инструментов ----------
void RenderOverlayUI() {
    ImGui::Begin("Pown Tools", nullptr,
                 ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoCollapse);

    ImGui::TextColored(ImVec4(0.45f, 0.56f, 0.14f, 1.0f), "Pownlauncher Overlay");
    ImGui::Separator();

    // 1. Браузер (URL откроется в браузере по умолчанию)
    if (ImGui::Button("Browser", ImVec2(140, 30))) {
        ShellExecuteA(nullptr, "open", "https://www.google.com", nullptr, nullptr, SW_SHOWDEFAULT);
    }
    // 2. Калькулятор Windows
    if (ImGui::Button("Calculator", ImVec2(140, 30))) {
        ShellExecuteA(nullptr, "open", "calc.exe", nullptr, nullptr, SW_SHOWDEFAULT);
    }
    // 3. Блокнот
    if (ImGui::Button("Notepad", ImVec2(140, 30))) {
        ShellExecuteA(nullptr, "open", "notepad.exe", nullptr, nullptr, SW_SHOWDEFAULT);
    }
    // 4. Таймер (приложение «Часы» Windows, вкладка «Таймер»)
    if (ImGui::Button("Timer", ImVec2(140, 30))) {
        ShellExecuteA(nullptr, "open", "ms-clock:", nullptr, nullptr, SW_SHOWDEFAULT);
    }

    ImGui::Separator();
    ImGui::TextDisabled("[F2] show/hide overlay");

    ImGui::End();
}

// ---------- установка хука ----------
void InitHook() {
    // opengl32.dll уже загружена игрой; GetModuleHandle безопаснее LoadLibrary
    HMODULE hGL = GetModuleHandleA("opengl32.dll");
    if (!hGL) return;

    void* pSwapBuffers = GetProcAddress(hGL, "wglSwapBuffers");
    if (!pSwapBuffers) return;

    if (MH_Initialize() != MH_OK) return;
    if (MH_CreateHook(pSwapBuffers, &Hooked_wglSwapBuffers,
                      reinterpret_cast<void**>(&o_wglSwapBuffers)) != MH_OK) return;
    MH_EnableHook(pSwapBuffers);
}

// ---------- очистка при выгрузке ----------
void Cleanup() {
    if (g_ImGuiInitialized) {
        if (o_WndProc && g_GameHwnd)
            SetWindowLongPtr(g_GameHwnd, GWLP_WNDPROC, (LONG_PTR)o_WndProc);
        ImGui_ImplOpenGL3_Shutdown();
        ImGui_ImplWin32_Shutdown();
        ImGui::DestroyContext();
        g_ImGuiInitialized = false;
    }
    MH_DisableHook(MH_ALL_HOOKS);
    MH_Uninitialize();
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        g_Module = hModule;
        InitHook();                                   // лёгкая инициализация — допустимо здесь
        CreateThread(nullptr, 0, HotkeyThread, nullptr, 0, nullptr);  // тяжёлое — в потоке
    } else if (reason == DLL_PROCESS_DETACH) {
        Cleanup();
    }
    return TRUE;
}
