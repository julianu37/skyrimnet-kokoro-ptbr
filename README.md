# SkyrimNet Kokoro PT-BR

Adaptador local de TTS **Kokoro (pt-BR)** para o **SkyrimNet**.

O SkyrimNet usa Kokoro **apenas no narrador** (é um caminho fixo no
`TtsEnginePool::Generate`, atrás de `tts.useKokoroForNarrator`). O Kokoro não é
um engine selecionável e não existe configuração para NPCs. Este projeto
resolve isso sem patch: ele implementa o contrato do engine **XTTS** que o
SkyrimNet já fala, e responde com Kokoro.

```
SkyrimNet (engine XTTS)  ->  este servidor  ->  Kokoro ONNX (pt-BR)
   POST /tts_to_audio/          mapeia voz         pf_dora / pm_alex / pm_santa
```

## Requisitos

- Windows 10/11 x64
- Python 3.12 (o `onnxruntime` ainda não tem wheel para 3.15)
- ~400 MB de disco (modelo ONNX + vozes)
- CPU: roda bem em 4–6 threads; GPU não é necessária (e evita disputar VRAM com o LLM)

## Instalação

```bat
setup.bat
```

O script cria a `.venv` com Python 3.12, instala as dependências, baixa
`kokoro-v1.0.onnx` + `voices-v1.0.bin` e valida as vozes pt-BR.

## Uso

```bat
start.bat
```

O servidor sobe em `http://127.0.0.1:8020`. Teste:

```powershell
Invoke-RestMethod http://127.0.0.1:8020/health
```

No SkyrimNet: **Settings → Voice/TTS**

| Campo | Valor |
|---|---|
| TTS Engine | `XTTS` |
| Endpoint | `http://127.0.0.1:8020` |
| Language | `pt` (o adapter força `pt-br`) |

O narrador pode continuar no Kokoro nativo (`Use Kokoro For Narrator`) ou ser
unificado neste adapter — nesse caso desligue a opção nativa.

## Mapeamento de vozes

O Kokoro tem vozes fixas (não clona). O adapter recebe a chave do falante
(`speaker_wav`, ex.: `male_nord`, `female_elf`, `narrator`) e escolhe uma voz
estável:

| Falante | Voz |
|---|---|
| narrador | `pf_dora` |
| player | `pm_alex` |
| feminino | `pf_dora` |
| masculino | `pm_alex` ou `pm_santa` (hash estável) |
| desconhecido | `pf_dora` (não adivinha gênero) |

Como pt-BR tem só 3 vozes, o adapter aplica uma variação determinística de
velocidade (`speed_jitter`) por NPC. Para timbre, use o sistema de **Voice
Effects** do próprio SkyrimNet (pitch/formant) por voice type.

Overrides explícitos em `config.yaml`:

```yaml
overrides:
  male_nord: pm_alex
  female_elf: pf_dora
  narrator: pf_dora
```

## Endpoints

| Método | Rota | Função |
|---|---|---|
| `GET` | `/health` | status, vozes, sample rate |
| `POST` | `/create_and_store_latents` | recebe o sample de referência (ignorado; opcionalmente arquivado) |
| `POST` | `/tts_to_audio/` | `{text, speaker_wav, language}` → WAV |

O sample de referência é aceito para o SkyrimNet não cair no fluxo legado, mas
o Kokoro usa a voz mapeada — não há clonagem.

## Diagnóstico

- Log: `logs/adapter.log` (inclui `synth=`, `audio=` e `rtf=`)
- Primeira chamada após o start é mais lenta (warm-up roda em background)
- Porta ocupada: altere `server.port` em `config.yaml`
- Firewall: o servidor escuta só em `127.0.0.1`

## Estrutura

```
kokoro-skyrimnet/
├── server.py          # FastAPI + Kokoro
├── config.yaml        # vozes, idioma, overrides
├── requirements.txt
├── setup.ps1/.bat     # venv + deps + modelos
├── start.bat
├── tools/check_install.py
├── models/            # baixado no setup (git-ignored)
└── logs/
```

## Licença

MIT. Kokoro-82M é Apache 2.0; o modelo é baixado do release oficial do
`kokoro-onnx`.
