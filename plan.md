# Plano de Engenharia — Charter v1.8: Deteccao Harmonica e Acordes Refinados

**Autor:** Cadence (o maestro da nota certa)  
**Data:** 2026-11-02  
**Escopo:** `core/analyzer.py`, `core/charter.py`, `tests/`  
**Objetivo:** Elevar o feel do chart de 30% para 80% do hand-charted profissional, implementando deteccao harmonica contextual e dedilhados ergonomicos de guitarra sem violar nenhum dos 205 testes existentes.

---

## 1. Diagnostico e Gargalos do Charter v1.7

A analise minuciosa de `core/analyzer.py` e `core/charter.py` revelou as seguintes limitacoes no modelo atual:

1. **Modulo Aritmetico Cego (`% 5`)**:
   - Atualmente, as notas secundarias sao escolhidas com base em `(root_fret + 2) % 5` ou `candidate = secondary_pc % 5`.
   - Como `secondary_pc` e uma classe de altura cromatica de 0 a 11 (C ate B), aplicar modulo 5 produz saltos atonais e ilogicos na escala de 5 botoes de Clone Hero.
   - Pior ainda: quando o traste raiz e 4 (Laranja), `(4 + 2) % 5 = 1` (Vermelho), criando o bizarro acorde Laranja + Vermelho com pulo de duas casas intermediarias no ar, algo quase inexistente no repertorio oficial de Guitar Hero / Rock Band.

2. **Croma Pontual e Ruidoso**:
   - `core/analyzer.py` apenas extrai `chroma_cqt` em frames individuais no instante do onset.
   - Um unico frame de transiente sofre com vazamento percussivo e harmonicos superiores da fundamental (overtones), levando o `argmax` a apontar notas espurias ou quintas como se fossem fundamentais.

3. **Monotonia Harmonica**:
   - Todos os acordes possuem praticamente o mesmo formato mecanico, sem diferenciacao entre acordes maiores, menores, power chords de 5a e acordes abertos com baixo sustentado.

---

## 2. Arquitetura da Solucao v1.8

### 2.1. Motor de Deteccao Harmonica Nativo de Zero Dependencia Externa
Para manter compatibilidade absoluta com o `.exe` standalone e evitar inflar a distribuicao com 2 GB de PyTorch ou depender de bibliotecas C++ (`vamp-plugin-sdk`) que quebram no Windows em modo congelado, implementaremos um analisador harmonico completo baseado nas ferramentas ja homologadas (`librosa`, `scipy`, `numpy`):

1. **Chroma CENS / CQT Filtrado no Tempo (`core/analyzer.py`)**:
   - Aplicar suavizacao temporal e normalizacao de energia (Chroma Energy Normalized Statistics) ao redor da janela de cada batida/onset.
   - Reducao drastica de ruidos de transientes e batidas percussivas.

2. **Dicionario de Templates de Classes de Altura (PCP Templates)**:
   - Construir matriz de correlacao de acordes com 48 templates padronizados:
     - **Power Chords (5ths)**: `[0, 7]` (Root + Quinta Justa)
     - **Triades Maiores**: `[0, 4, 7]` (Root + Terca Maior + Quinta)
     - **Triades Menores**: `[0, 3, 7]` (Root + Terca Menor + Quinta)
     - **Sus2 / Sus4**: `[0, 2, 7]`, `[0, 5, 7]`
   - Em cada frame de onset, calcular o score de similaridade por cosseno com os templates para determinar:
     - `chord_root`: Nota fundamental real (0..11)
     - `chord_type`: "5", "maj", "min", "sus"
     - `confidence`: Nivel de certeza da harmonia

3. **Hook Opcional para Chordino (Vamp)**:
   - Se o usuario possuir o pacote `vamp` e a DLL do `nnls-chroma` instalados no sistema, um dispatcher opcional podera consumir as saidas do Chordino diretamente. Caso contrario, o fallback nativo com Librosa assume sem falhas.

4. **Preservacao do Dataclass `Analysis`**:
   - Adicionar atributos opcionais com valores padrao (`chords: List[tuple] = field(default_factory=list)`, `key: str = ""`) em `core/analyzer.py`.
   - Garantir que todos os testes e fixtures que instanciam `Analysis(...)` continuem funcionando sem alteracoes.

---

### 2.2. Sistema de Voicing e Dedilhado Ergonomico (`core/charter.py`)

Substituir o algoritmo anterior por regras de voicing contextual inspiradas nos hand-charts oficiais:

1. **Power Chords (Quintas - Tipo "5")**:
   - Traste raiz 0 (Verde)   -> `(0, 2)` (Verde + Amarelo)
   - Traste raiz 1 (Vermelho) -> `(1, 3)` (Vermelho + Azul)
   - Traste raiz 2 (Amarelo)  -> `(2, 4)` (Amarelo + Laranja)
   - Traste raiz 3 (Azul)     -> `(1, 3)` ou `(2, 3)` (voicing invertido ou compacto, nunca wrap para Verde)
   - Traste raiz 4 (Laranja)  -> `(2, 4)` ou `(3, 4)` (dupla superior limpa)

2. **Diferenciacao Emocional Maior vs Menor**:
   - **Acordes Menores ("min")**: Voicings mais tensos e proximos (intervalos contiguos `(0, 1)`, `(1, 2)`, `(2, 3)`), refletindo o tom sombrio/grave tipico de faixas trap e rock.
   - **Acordes Maiores ("maj")**: Voicings mais abertos e ressonantes (`(0, 2)`, `(1, 3)`, `(2, 4)`).

3. **Power Chords Triplos em Momentos de Alta Energia**:
   - Em beats com `RMS >= p80`, adicionar a oitava superior sem quebrar a progressao:
     - Raiz 0 -> `(0, 2, 3)`
     - Raiz 1 -> `(1, 3, 4)`

4. **Coerencia Melodica ao Longo do Compasso**:
   - Se um acorde for sustentado ou repetido dentro da mesma barra, preservar o voicing para dar consistencia fisica ao jogador em vez de trocar aleatoriamente a cada batida.

---

## 3. Matriz de Compatibilidade e Protecao dos 205 Testes

O plano foi desenhado para respeitar estritamente cada teste da suite:

| Teste / Invariante | Requisito Estrito | Como o v1.8 Garante |
|---|---|---|
| `test_charter_quality.py` (Chord Rate) | `chord_rate` em `[0.20, 0.35]` | Mantem as metas de secao (`_sec_target`) e o cap v1.7 de seguranca (`target_rate = 0.28`, max 0.32). |
| `test_charter_quality.py` (HOPO Rate) | HOPO count == 0 (`fret != 5`) | Nenhuma nota no traste 5 e emitida; o motor de HOPO forçado continua desativado. |
| `test_charter_quality.py` (Fret 0 share) | Traste Verde <= 35% | O voicer ergonômico distribui os acordes uniformemente entre 0, 1, 2, 3 e 4. |
| `test_charter_quality.py` (Fret balance) | Todos trastes 0..4 >= 5% | Distribuicao balanceada garantida pela combinacao de pitch contour + voicer. |
| `test_downcharts.py` (Hard, Med, Easy) | Downcharts limpos e estritos | `gen_downcharts` continua extraindo o traste raiz principal e aplicando os limites de traste (max 3 no Medium, max 2 no Easy). |
| `test_sustain_profile.py` | Anti-colisao e 8 ticks margin | `_clean_sustains` continua sendo chamado no final de todas as dificuldades. |
| `test_bpm_density.py` | Densidade controlada por BPM | Mantem os filtros de `bpm_cap` e `bpm_subdiv`. |
| `test_charter_v12.py` | SyncTrack multi-tempo >= 5 | SyncTrack permanece intacto gerando marcadores B em cada batida detectada. |

---

## 4. Fases de Execucao

1. **Fase 1 (Harmonic Engine)**:
   - Adicionar funcoes de template matching harmônico (`_detect_chords_librosa`) em `core/analyzer.py`.
   - Adicionar campo opcional `chords` em `Analysis`.
   - Validar com testes unitarios pontuais.

2. **Fase 2 (Ergonomic Voicer no Charter)**:
   - Implementar tabela de voicer ergonômico no `core/charter.py`.
   - Conectar os acordes detectados na injecao de beats e onsets com RMS alto.
   - Manter o cap de secao e o filtro v1.7 de teto `0.32`.

3. **Fase 3 (Verificacao e Sanidade)**:
   - Executar os 205 testes de `pytest` e confirmar 100% de sucesso.
   - Executar `core/chart_compare.py` para comparar o resultado com os priors das 5 faixas de referencia.
   - Construir o `.exe` via `make_release.ps1` e testar no Clone Hero.
