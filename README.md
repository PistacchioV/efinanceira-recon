# e-Financeira Recon

Batimento e-Financeira entre os arquivos `MMAA_TRD` e `MMAA_PTP` (Python/Flask, HTML, JS).

## Rodar

- **Windows:** dois cliques em `iniciar.bat` — instala o `requirements.txt`, sobe o servidor (waitress, porta 5070) e abre o navegador. `iniciar.bat noinstall` pula a instalação.
- **Manual:** `pip install -r requirements.txt` e `python app.py` → http://127.0.0.1:5070

## Regras

- **IDs:** coluna J do TRD (`INOA-1310004198`) × coluna A do PTP, casados pelo miolo numérico.
  - só no TRD → `Allege on PTP side`
  - só no PTP → `Allege on TRD side`
- **Diferenças (TRD − PTP):** N−B, P−C, Q−D, T−E, U−F, V−G. Datas em dias; vazio em coluna de valor conta como zero; códigos diferentes → `DIVERGENTE`.
- **Saída:** `MMAA_e-financeira_recon.xlsx` com as colunas A–T da especificação, status em U–V e aba `Resumo`.

`exemplos/` traz planilhas sintéticas para teste.
