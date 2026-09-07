@echo off
chcp 65001 >nul
title SPECTER CORE 24/7 - SISTEMA DE SUPERVISAO RESILIENTE

:: Diretorio base relativo e configuracao do Core
set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
set "CORE_DIR=%BASE_DIR%\Core"
if not exist "%CORE_DIR%" set "CORE_DIR=C:\specter\Core"
set "LOG_PATH=%CORE_DIR%\supervisor_247.log"

:: Detectar ou configurar Python no PATH se necessario
where python >nul 2>&1
if errorlevel 1 (
    if exist "C:\Users\USER\AppData\Local\Programs\Python\Python312_Clean\python.exe" (
        set "PATH=C:\Users\USER\AppData\Local\Programs\Python\Python312_Clean;%PATH%"
    )
)

:: Se executado com argumento direto, nao bloquear em pausa interativa
set "NON_INTERACTIVE="
if not "%1"=="" set "NON_INTERACTIVE=1"

if "%1"=="1" goto OPCAO1_AUTO
if "%1"=="2" goto OPCAO2
if "%1"=="3" goto OPCAO3
if "%1"=="4" goto OPCAO4
if /i "%1"=="run" goto OPCAO1_AUTO
if /i "%1"=="health" goto OPCAO2
if /i "%1"=="logs" goto OPCAO3
if /i "%1"=="stop" goto OPCAO4

:MENU
cls
echo =======================================================================
echo          SPECTER CORE 24/7 - SUPERVISOR SOBERANO \ RESILIENTE
echo                       Owner: Guilherme Peralta Novaes
echo =======================================================================
echo.
echo   [1] Iniciar Servidor Completo Gateway + Broker 24/7
echo   [2] Executar Health Check
echo   [3] Ver Logs ao Vivo
echo   [4] Parar Servidor com seguranca
echo   [0] Sair
echo.
echo =======================================================================
set /p OPCAO="Escolha uma opcao [0-4]: "

if "%OPCAO%"=="1" goto OPCAO1
if "%OPCAO%"=="2" goto OPCAO2
if "%OPCAO%"=="3" goto OPCAO3
if "%OPCAO%"=="4" goto OPCAO4
if "%OPCAO%"=="0" goto SAIR

echo [!] Opcao invalida. Digite de 0 a 4.
timeout /t 2 >nul
goto MENU

:OPCAO1
cls
echo =======================================================================
echo   [1] INICIAR SERVIDOR COMPLETO GATEWAY + BROKER 24/7
echo =======================================================================
echo [*] Escolha o modo de execucao:
echo.
echo     [1] Nesta janela (Visualizar batimento cardiaco em tempo real)
echo     [2] Em janela dedicada (Segundo plano continuo 24/7)
echo     [V] Voltar ao menu principal
echo.
set /p MODO="Selecione o modo [1, 2, V]: "

if "%MODO%"=="1" goto OPCAO1_AUTO
if "%MODO%"=="2" goto OPCAO1_BG
if /i "%MODO%"=="v" goto MENU
goto OPCAO1_AUTO

:OPCAO1_AUTO
cls
echo =======================================================================
echo   INICIANDO SUPERVISOR SPECTER CORE 24/7 (MODO CONSOLE)
echo =======================================================================
echo [*] Inicializando Gateway OpenAI (:8080) e Broker SQLite WAL...
echo [*] Pressione Ctrl+C para interromper a qualquer momento.
echo.
cd /d "%CORE_DIR%"
python specter_supervisor_247.py --with-gateway
echo.
echo [*] Processo finalizado.
if not defined NON_INTERACTIVE (
    pause
    goto MENU
)
exit /b 0

:OPCAO1_BG
cls
echo =======================================================================
echo   INICIANDO SERVIDOR EM JANELA DEDICADA 24/7
echo =======================================================================
start "SPECTER CORE 24/7 ENGINE" cmd /k "chcp 65001 >nul && cd /d ""%CORE_DIR%"" && title SPECTER CORE 24/7 DAEMON && python specter_supervisor_247.py --with-gateway"
echo.
echo [OK] Servidor despachado em nova janela com sucesso!
echo [*] O supervisor continuara em execucao continua 24/7.
echo [*] Use a Opcao [2] para Health Check ou Opcao [3] para logs ao vivo.
echo.
if not defined NON_INTERACTIVE (
    pause
    goto MENU
)
exit /b 0

:OPCAO2
cls
cd /d "%CORE_DIR%"
python specter_supervisor_247.py --health
echo.
if not defined NON_INTERACTIVE (
    pause
    goto MENU
)
exit /b 0

:OPCAO3
cls
echo =======================================================================
echo   [3] LOGS AO VIVO - TRANSMISSAO EM TEMPO REAL
echo =======================================================================
echo [*] Conectando ao fluxo supervisor_247.log...
echo [*] Pressione Ctrl+C para interromper a exibicao e voltar ao menu.
echo -----------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$log = '%LOG_PATH%'; if (Test-Path $log) { Get-Content $log -Wait -Tail 30 } else { Write-Host 'Arquivo de log nao existe ainda.' }"
echo -----------------------------------------------------------------------
if not defined NON_INTERACTIVE (
    pause
    goto MENU
)
exit /b 0

:OPCAO4
cls
cd /d "%CORE_DIR%"
python specter_supervisor_247.py --stop
echo.
if not defined NON_INTERACTIVE (
    pause
    goto MENU
)
exit /b 0

:SAIR
cls
echo [*] Encerrando Centro de Controle Specter Core 24/7. Ate logo!
timeout /t 1 >nul
exit /b 0
