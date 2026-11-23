/*
 * MiroFish.exe - the desktop launcher (Windows x64)
 *
 * Responsibilities:
 *   1. locate the Python interpreter (two install shapes are supported):
 *        runtime\venv\Scripts\pythonw.exe    <- the virtual environment created by Setup.bat on the first run
 *        runtime\python\pythonw.exe          <- the embeddable Python bundled in the offline build
 *   2. on the first run, when no interpreter is found, ask the user and run Setup.bat to initialize it.
 *   3. run app\app.py with the install directory as the working directory, forward the command-line arguments and wait for it to exit.
 *   4. keep the console window with --console, so errors are visible.
 *
 * Cross-compiling (Linux, with zig):
 *   zig cc -target x86_64-windows-gnu -O2 -municode -mwindows \
 *          -o MiroFish.exe launcher.c app.rc -lole32 -lshell32 -luser32
 */

#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>

#define PATH_CAP 4096

static void dirname_of(wchar_t *path)
{
    wchar_t *p = wcsrchr(path, L'\\');
    if (p) *p = L'\0';
}

static int file_exists(const wchar_t *path)
{
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static void show_error(const wchar_t *message, const wchar_t *detail)
{
    wchar_t buf[4096];
    _snwprintf(buf, 4096, L"%ls\n\n%ls", message, detail ? detail : L"");
    MessageBoxW(NULL, buf, L"MiroFish", MB_OK | MB_ICONERROR);
}

static void append_arg(wchar_t *out, size_t cap, const wchar_t *arg)
{
    size_t used = wcslen(out);
    if (used + 2 >= cap) return;
    out[used++] = L' ';
    out[used++] = L'"';
    out[used] = L'\0';
    for (const wchar_t *p = arg; *p && used + 4 < cap; ++p) {
        if (*p == L'"') {
            out[used++] = L'\\';
            out[used++] = L'"';
        } else {
            out[used++] = *p;
        }
        out[used] = L'\0';
    }
    if (used + 2 < cap) {
        out[used++] = L'"';
        out[used] = L'\0';
    }
}

static int has_flag(int argc, wchar_t **argv, const wchar_t *flag)
{
    for (int i = 1; i < argc; ++i) {
        if (_wcsicmp(argv[i], flag) == 0) return 1;
    }
    return 0;
}

/* Returns 1 when an interpreter was found */
static int find_python(const wchar_t *app_dir, int console_mode, wchar_t *out)
{
    const wchar_t *candidates[] = {
        L"runtime\\venv\\Scripts\\pythonw.exe",
        L"runtime\\venv\\Scripts\\python.exe",
        L"runtime\\python\\pythonw.exe",
        L"runtime\\python\\python.exe",
        L"resources\\runtime\\python\\pythonw.exe",
        NULL
    };
    for (int i = 0; candidates[i]; ++i) {
        const wchar_t *name = candidates[i];
        if (console_mode) {
            /* Console mode prefers python.exe */
            if (wcsstr(name, L"pythonw.exe")) continue;
        }
        _snwprintf(out, PATH_CAP, L"%ls\\%ls", app_dir, name);
        if (file_exists(out)) return 1;
    }
    /* Fallback: the environment variable */
    wchar_t env_py[PATH_CAP];
    DWORD n = GetEnvironmentVariableW(L"MIROFISH_PYTHON", env_py, PATH_CAP);
    if (n > 0 && n < PATH_CAP && file_exists(env_py)) {
        wcscpy(out, env_py);
        return 1;
    }
    return 0;
}

static int run_setup(const wchar_t *app_dir)
{
    wchar_t script[PATH_CAP];
    _snwprintf(script, PATH_CAP, L"%ls\\Setup.bat", app_dir);
    if (!file_exists(script)) return 0;

    int answer = MessageBoxW(NULL,
        L"The runtime has to be initialized on first run.\n\n"
        L"Python and the dependencies will be downloaded (about 500 MB-1 GB, depending on the network),\n"
        L"and later launches will not need the network at all.\n\n"
        L"Initialize now?",
        L"MiroFish first run", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON1);
    if (answer != IDYES) return 0;

    /* Long install paths (C:\Users\<name>\Downloads\MiroFish\...) must not be truncated:
       a clipped command line makes Setup.bat silently do nothing, which used to show up as
       "no Python runtime found". */
    static wchar_t cmd[32768];
    /* In GUI mode this process has no console, so Setup.bat would run detached and its window
       would vanish ("flashes past"). Give it its own console window and keep it open (cmd /k)
       so every message stays readable until the user closes it. */
    _snwprintf(cmd, 32768,
               L"cmd.exe /k title MiroFish setup ^& echo Running the first-run setup; "
               L"this window stays open so you can read the result.^& echo.^& "
               L"\"%ls\" --no-pause",
               script);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    /* The working directory must exist, otherwise CreateProcess fails outright */
    const wchar_t *cwd = app_dir;
    if (!file_exists(app_dir)) cwd = NULL;

    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, CREATE_NEW_CONSOLE, NULL, cwd, &si, &pi)) {
        wchar_t msg[512];
        _snwprintf(msg, 512, L"Setup.bat could not be started (error %lu).", GetLastError());
        show_error(msg,
                   L"Please run Setup.bat in the install directory by hand: it prints the real error.");
        return 0;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    /* cmd /k leaves the console open, so the user can always read what happened;
       that window is closed by hand, not by us. */
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code == 0;
}

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PWSTR lpCmdLine, int nCmdShow)
{
    (void)hInstance; (void)hPrevInstance; (void)lpCmdLine; (void)nCmdShow;

    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);

    wchar_t exe_path[PATH_CAP];
    DWORD len = GetModuleFileNameW(NULL, exe_path, PATH_CAP);
    if (len == 0 || len >= PATH_CAP) {
        show_error(L"Cannot determine the program directory.", NULL);
        return 2;
    }
    wchar_t app_dir[PATH_CAP];
    wcscpy(app_dir, exe_path);
    dirname_of(app_dir);

    int want_console = has_flag(argc, argv, L"--console");
    int headless = has_flag(argc, argv, L"--headless");
    int want_browser = has_flag(argc, argv, L"--browser");
    int console_mode = want_console || headless || want_browser;

    wchar_t python[PATH_CAP];
    if (!find_python(app_dir, console_mode, python)) {
        if (has_flag(argc, argv, L"--no-setup") || !run_setup(app_dir)) {
            if (!find_python(app_dir, console_mode, python)) {
                /* Setup failed: rename the leftover downloads so a .part collision does not break the next attempt */
                wchar_t part[PATH_CAP];
                _snwprintf(part, PATH_CAP, L"%ls\\runtime\\.cache", app_dir);
                if (file_exists(part)) {
                    wchar_t aside[PATH_CAP];
                    _snwprintf(aside, PATH_CAP, L"%ls.stale", part);
                    MoveFileExW(part, aside, MOVEFILE_REPLACE_EXISTING);
                }
                show_error(L"MiroFish setup did not finish: no Python runtime found.",
                           L"Most common causes:\n"
                           L"  1) antivirus or Windows 'Controlled folder access' blocked the download or the venv\n"
                           L"     (allow the MiroFish folder, then run it again)\n"
                           L"  2) not enough disk space (a full install needs about 3 GB free, the slim one about 1.5 GB)\n"
                           L"  3) no Python 3.10-3.13 and winget is unavailable\n"
                           L"  4) the network dropped (running it again resumes automatically)\n\n"
                           L"To see the real error: double-click MiroFish-Console.bat (it keeps the window open),\n"
                           L"or run Setup.bat in the install directory by hand.\n"
                           L"The log is in %APPDATA%\\MiroFish\\logs\\desktop.log.");
                return 3;
            }
        }
        if (!find_python(app_dir, console_mode, python)) {
            show_error(L"Setup failed: the Python runtime is still not found.",
                       L"Please read the error shown in the Setup.bat window.");
            return 3;
        }
    }

    wchar_t script[PATH_CAP];
    _snwprintf(script, PATH_CAP, L"%ls\\app\\app.py", app_dir);
    if (!file_exists(script)) {
        show_error(L"The MiroFish install is incomplete: app\\app.py is missing.",
                   L"Please extract the installer again.");
        return 4;
    }

    static wchar_t cmd[32768];
    cmd[0] = L'\0';
    append_arg(cmd, 32768, python);
    append_arg(cmd, 32768, script);
    for (int i = 1; i < argc; ++i) {
        append_arg(cmd, 32768, argv[i]);
    }

    if (want_console && GetConsoleWindow() == NULL) {
        AllocConsole();
        SetConsoleTitleW(L"MiroFish log");
        FILE *f;
        freopen_s(&f, "CONOUT$", "w", stdout);
        freopen_s(&f, "CONOUT$", "w", stderr);
    }

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    DWORD flags = CREATE_UNICODE_ENVIRONMENT;
    if (!CreateProcessW(python, cmd, NULL, NULL, want_console ? TRUE : FALSE,
                        flags, NULL, app_dir, &si, &pi)) {
        wchar_t detail[PATH_CAP * 2];
        _snwprintf(detail, PATH_CAP * 2, L"CreateProcess failed (error code %lu)\n%ls",
                   GetLastError(), cmd);
        show_error(L"Cannot start the MiroFish backend.", detail);
        return 5;
    }

    WaitForSingleObject(pi.hProcess, INFINITE);

    DWORD exit_code = 0;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    LocalFree(argv);

    if (want_console) {
        wprintf(L"\n[launcher] the backend exited with code %lu. Press Enter to close the window...\n", exit_code);
        getchar();
    }
    return (int)exit_code;
}
