@echo off
REM ============================================================================
REM  e-Financeira Recon  (Windows)
REM
REM  Instala as dependencias (requirements.txt), sobe o servidor Waitress na
REM  porta 5070 e abre  http://127.0.0.1:5070/  no navegador sozinho.
REM  A rede chega pelo  http://IP-da-maquina:5070/
REM
REM  Uso:  duplo clique.
REM        iniciar.bat noinstall   ... pula a instalacao (util offline)
REM
REM  Nao instala Python. Se o desta estacao estiver num lugar que o script nao
REM  procura, aponte:   set EFIN_PYTHON=C:\caminho\para\python.exe
REM ============================================================================

setlocal
set "PORTA=5070"
if defined EFIN_PORTA set "PORTA=%EFIN_PORTA%"
set "URL=http://127.0.0.1:%PORTA%/"
REM  porta do agente de proxy local da estacao (localproxy-cfg)
set "PROXY_LOCAL_PORTA=9443"

REM ---------------------------------------------------------------------------
REM  Reentrada: chamado com --abrir, este mesmo .bat nao sobe nada. So espera a
REM  porta responder e abre o navegador.
REM ---------------------------------------------------------------------------
if /I "%~1"=="--abrir" goto :ABRIR_NAVEGADOR

REM ---------------------------------------------------------------------------
REM  UNC-safe: `cd /d` nao aceita caminho de rede. O pushd mapeia o share numa
REM  letra temporaria.
REM ---------------------------------------------------------------------------
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [ERRO] Nao consegui acessar a pasta: %~dp0
    pause
    exit /b 1
)

set "BASE=%~dp0"
title e-Financeira Recon

echo [DICA] Se o titulo da janela comecar com "Select", o console esta em modo
echo        de selecao e o processo fica congelado. Aperte Esc para destravar.
echo.

REM ---------------------------------------------------------------------------
REM  Localiza o Python. NAO instala nada.
REM  Caminhos conhecidos primeiro (if exist); o PATH passa por teste de
REM  execucao por causa do atalho da Microsoft Store em WindowsApps.
REM ---------------------------------------------------------------------------
set "PY="
if defined EFIN_PYTHON if exist "%EFIN_PYTHON%" set "PY=%EFIN_PYTHON%"
if not defined PY if exist "%BASE%.venv\Scripts\python.exe" set "PY=%BASE%.venv\Scripts\python.exe"
if not defined PY if exist "%BASE%Scripts\python.exe"       set "PY=%BASE%Scripts\python.exe"

for %%n in (3.13 3.12 3.11 3.10) do (
    if not defined PY if exist "%USERPROFILE%\ds\tools\python%%n\latest\python.exe" set "PY=%USERPROFILE%\ds\tools\python%%n\latest\python.exe"
    if not defined PY if exist "%USERPROFILE%\ds\tools\python%%n\python.exe"        set "PY=%USERPROFILE%\ds\tools\python%%n\python.exe"
)

for %%n in (313 312 311 310 39) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%n\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%n\python.exe"
    if not defined PY if exist "%ProgramFiles%\Python%%n\python.exe"                 set "PY=%ProgramFiles%\Python%%n\python.exe"
    if not defined PY if exist "C:\Python%%n\python.exe"                             set "PY=C:\Python%%n\python.exe"
)

if defined PY if defined EFIN_DEBUG echo [DEBUG] achado por caminho: %PY%

call :TESTAR_PYTHON "py"
call :TESTAR_PYTHON "python"
call :TESTAR_PYTHON "python3"

if not defined PY echo [INFO] Nao achei nos caminhos usuais; varrendo ds\tools...
call :VARRER "%USERPROFILE%\ds\tools"
call :VARRER "%LOCALAPPDATA%\Programs\Python"

if not defined PY (
    echo.
    echo [ERRO] Nenhum Python 3.9+ foi encontrado.
    echo.
    echo        Aponte o caminho e rode de novo:
    echo          set EFIN_PYTHON=C:\Users\seu.usuario\ds\tools\python3.12\latest\python.exe
    echo.
    echo        Para ver cada candidato testado:  set EFIN_DEBUG=1
    echo.
    popd
    pause
    exit /b 1
)
echo [INFO] Python: %PY%
for /f "usebackq delims=" %%v in (`"%PY%" -c "import sys;print(sys.version.split()[0])" 2^>nul`) do echo [INFO] Versao: %%v

REM ---------------------------------------------------------------------------
REM  Proxy para o pip. Variavel do perfil de outro terminal nao chega ao cmd do
REM  duplo clique; se o agente local estiver escutando, ele e usado.
REM ---------------------------------------------------------------------------
if defined EFIN_PROXY (
    set "HTTP_PROXY=%EFIN_PROXY%"
    set "HTTPS_PROXY=%EFIN_PROXY%"
)
if not defined HTTPS_PROXY if not defined HTTP_PROXY (
    netstat -an | findstr /C:":%PROXY_LOCAL_PORTA% " | findstr /I /C:"LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "HTTP_PROXY=http://127.0.0.1:%PROXY_LOCAL_PORTA%"
        set "HTTPS_PROXY=http://127.0.0.1:%PROXY_LOCAL_PORTA%"
        echo [INFO] Agente de proxy local detectado na porta %PROXY_LOCAL_PORTA%.
    )
)
if defined HTTPS_PROXY echo [INFO] Proxy: %HTTPS_PROXY%

REM ---------------------------------------------------------------------------
REM  Dependencias, best-effort: se o pypi estiver bloqueado, avisa e segue com
REM  o que ja esta instalado.
REM ---------------------------------------------------------------------------
if /I "%~1"=="noinstall" (
    echo [INFO] Instalacao de dependencias pulada ^(noinstall^).
) else (
    echo.
    echo [INFO] Instalando dependencias ^(requirements.txt^)...
    "%PY%" -m pip install -r "%BASE%requirements.txt" --timeout 10 --retries 1 --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo [AVISO] Nao consegui instalar/atualizar as dependencias ^(rede/pypi^).
        echo         Seguindo com o que ja esta instalado.
        echo.
    )
)

if not defined PYTHONPYCACHEPREFIX set "PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\EfinRecon\pycache"

REM  abre o navegador numa janela propria, que espera o servidor responder
start "e-Financeira Recon - navegador" /min "%~f0" --abrir

echo.
echo [e-Financeira Recon] http://127.0.0.1:%PORTA%  ^(waitress^)
echo                      Feche esta janela ou Ctrl+C para parar.
echo.
REM  Waitress e nao gunicorn: gunicorn nao roda no Windows. Um processo so,
REM  porque a analise para exportacao fica guardada na memoria do processo.
"%PY%" -c "import waitress" >nul 2>&1
if errorlevel 1 goto :SEM_WAITRESS
"%PY%" -m waitress --host=0.0.0.0 --port=%PORTA% --threads=8 app:app
goto :PAROU

:SEM_WAITRESS
echo [AVISO] waitress nao esta instalado; usando o servidor do Flask.
set "EFIN_PORTA=%PORTA%"
set "EFIN_HOST=0.0.0.0"
"%PY%" app.py

:PAROU

echo.
echo [INFO] O servidor parou.
popd
pause
exit /b


REM ===========================================================================
:VARRER
REM  Testa todo python.exe sob %~1, em qualquer profundidade.
REM ===========================================================================
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
echo [INFO]   varrendo %~1 ...
for /f "usebackq delims=" %%p in (`dir /b /s "%~1\python.exe" 2^>nul`) do call :TESTAR_PYTHON "%%p"
exit /b 0

REM ===========================================================================
:TESTAR_PYTHON
REM  Aceita %~1 como Python so se ele executar e for 3.9+.
REM  max(m,9)==m em vez de comparacao com sinal de maior: dentro de `for /f`
REM  o cmd trata o sinal como redirecionamento, mesmo entre aspas.
REM ===========================================================================
if defined PY exit /b 0
if "%~1"=="" exit /b 0
echo %~1 | findstr /I /C:"\WindowsApps\" >nul && (
    if defined EFIN_DEBUG echo [DEBUG] recusado ^(atalho da Microsoft Store^): %~1
    exit /b 0
)
set "MARCA="
for /f "usebackq delims=" %%r in (`"%~1" -c "import sys;v=sys.version_info;print('PY3OK' if v[0]==3 and max(v[1],9)==v[1] else 'VELHO')" 2^>nul`) do set "MARCA=%%r"
if /I not "%MARCA%"=="PY3OK" (
    if defined EFIN_DEBUG echo [DEBUG] recusado ^(nao respondeu^): %~1
    exit /b 0
)
if defined EFIN_DEBUG echo [DEBUG] aceito: %~1
set "PY=%~1"
exit /b 0


REM ===========================================================================
:ABRIR_NAVEGADOR
REM  Espera a porta responder antes de abrir a pagina (ate ~60s).
REM ===========================================================================
set "TENTATIVAS=0"
:ESPERAR
set /a TENTATIVAS+=1
if %TENTATIVAS% GTR 60 goto :ABRIR_ASSIM_MESMO
netstat -an | findstr /C:":%PORTA% " | findstr /I /C:"LISTENING" >nul 2>&1
if not errorlevel 1 goto :ABRIR_ASSIM_MESMO
ping -n 2 127.0.0.1 >nul 2>&1
goto :ESPERAR

:ABRIR_ASSIM_MESMO
start "" "%URL%"
exit /b
