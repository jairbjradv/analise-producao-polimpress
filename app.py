import streamlit as st
import pdfplumber
import pandas as pd
import re
import plotly.graph_objects as go

st.set_page_config(page_title="Análise de Produção - Polimpress", layout="wide")

# ── CSS de impressão ─────────────────────────────────────────────────────────
st.markdown("""
<style>
@media print {
    /* Oculta elementos de navegação do Streamlit */
    header, footer, [data-testid="stToolbar"],
    [data-testid="stSidebar"], [data-testid="stFileUploader"],
    [data-testid="stStatusWidget"], .stDeployButton,
    [data-testid="stDecoration"] { display: none !important; }

    /* Cada seção marcada com .pbreak começa em nova página */
    .pbreak { page-break-before: always; break-before: page; }

    /* Evita quebra no meio de tabelas e gráficos */
    [data-testid="stDataFrame"],
    [data-testid="stPlotlyChart"] { page-break-inside: avoid; break-inside: avoid; }

    /* Expande toda a largura */
    .block-container { max-width: 100% !important; padding: 0 !important; }
}
</style>
""", unsafe_allow_html=True)


def quebra_pagina():
    """Insere marcador de quebra de página visível apenas na impressão."""
    st.markdown('<div class="pbreak"></div>', unsafe_allow_html=True)


st.title("📊 Sistema de Análise de Produção Homem x Máquina")
st.subheader("Análise unificada de relatórios de produção por operador")

st.markdown("""
> 📄 **Relatório necessário:** utilize o **RPCP621 — Análise Resumida de Produção Agrupada por Quebra por Dia x Recurso x Funcionário x Item** — gere um PDF por turno e carregue todos juntos. O turno de cada operador é detectado automaticamente pelo relatório.

- 🏆 **Ranking de Operadores** — compara todos os operadores por **KG/hora** real trabalhada, eliminando diferenças de turno, sábados e dias trabalhados. Detalha o desempenho de cada operador dentro de cada máquina que utilizou.
- 🏭 **Ranking por Máquina** — mostra quais máquinas mais produziram (KG/dia e UN/dia), qual operador foi o melhor em cada uma e permite comparar lado a lado os operadores de uma mesma máquina.
- ⚖️ **Comparativo por Item** — selecione um produto e veja quem produziu mais (KG/hora) naquele item específico — comparação justa, mesma matéria-prima e mesmo produto.
- 🏅 **Top Produção** — ranking de quem produziu mais em volume total (KG e UN), tanto por operador quanto por máquina.
- 🎯 **Comparativo por Máquina** — análise de gap de produtividade por máquina: semáforo 🟢🟡🔴 para cada operador, potencial de ganho em KG/mês e projeção de quanto a produção total cresceria se todos atingissem o nível do melhor. Filtrável por turno.
- 📈 **Evolução Operador** — acompanha a evolução mês a mês de um operador específico em KG/hora, KG/dia e volume total.
- 📅 **KG / Dia** — visualiza a produção diária de cada operador em gráfico de barras, com filtro por período e máquina.
- 📊 **Resumo Geral** — visão consolidada do período: total de KG, UN, apontamentos, operadores e máquinas. Inclui validação do total do relatório.
- 📋 **Dados Brutos** — todos os registros extraídos dos PDFs com filtros por operador, turno e máquina, exportável em CSV.
""")

# Captura qualquer unidade: UN (unidade), KG (kilograma), MT (metro), etc.
_RE_ITEM_GERAL  = re.compile(r"^(\d+)\s*-\s*(.*?)\s+([\d\.]+,\d+)\s*(UN|KG|MT|M2|PC|CX|FD|RL|PT)\s+([\d\.]+,\d+)\s*$")
# Fallback legado (mantido por compatibilidade)
_RE_ITEM_UN     = re.compile(r"^(\d+)\s*-\s*(.*)\s+([\d\.]+,\d+)\s*UN\s+([\d\.]+,\d+)\s*$")
_RE_ITEM_KG     = re.compile(r"^(\d+)\s*-\s*(.*)\s+([\d\.]+,\d+)\s*KG\s+([\d\.]+,\d+)\s*$")
_RE_USUARIO     = re.compile(r"[Uu]su.{0,2}rio:\s*\d+\s*-\s*(.+)")
_RE_FUNCIONARIO = re.compile(r"^Funcion.{0,2}rio:\s*\d+\s*-\s*(.+)")
_RE_TURNO       = re.compile(r"Turno:\s*(\d+)")
_RE_DIA         = re.compile(r"Dia:\s*(\d{2}/\d{2}/\d{4})")
_RE_RECURSO     = re.compile(r"Recurso:\s*(.+)")
_RE_PERIODO     = re.compile(r"Per.{0,2}odo:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})(?:.*?Turno\*?:\s*(\d+))?")  # \*? captura "Turno:" e "Turno*:"

_HORAS: dict[tuple[int, bool], float] = {
    (1, False): 7 + 50 / 60,
    (1, True):  4 + 50 / 60,
    (2, False): 7 + 50 / 60,
    (2, True):  4 + 50 / 60,
    (3, False): 6 + 50 / 60,
    (3, True):  4 + 20 / 60,
}


def horas_turno(turno_str: str, data_dt) -> float:
    try:
        num = int(turno_str.replace("Turno", "").strip())
    except ValueError:
        return 7 + 50 / 60
    return _HORAS.get((num, data_dt.weekday() == 5), 7 + 50 / 60)


def horas_operador_periodo(turno_str: str, inicio_dt, fim_dt) -> float:
    """Total de horas produtivas de um turno ao longo de um período (Seg–Sáb)."""
    total = 0.0
    current = inicio_dt
    while current <= fim_dt:
        if current.weekday() < 6:  # Seg–Sáb, exclui Domingo
            total += horas_turno(turno_str, current)
        current += pd.Timedelta(days=1)
    return total


def dias_trabalhados_no_periodo(inicio_dt, fim_dt) -> int:
    """Conta dias úteis (Seg–Sáb) no intervalo do período."""
    count = 0
    current = inicio_dt
    while current <= fim_dt:
        if current.weekday() < 6:
            count += 1
        current += pd.Timedelta(days=1)
    return count


def nome_curto(nome_completo: str) -> str:
    """'LUIS FELIPE DA SILVA ABATI' → 'Luis A.'"""
    partes = [p for p in nome_completo.strip().split() if len(p) > 1]
    if not partes:
        return nome_completo
    primeiro = partes[0].capitalize()
    inicial_ultimo = partes[-1][0].upper()
    return f"{primeiro} {inicial_ultimo}."


def _parse_br_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def bar_chart(labels, values, fmt=".1f", cor="#4C9BE8", height=340, max_show=None):
    """Gráfico de barras com valor dentro da barra. max_show=N exibe N barras e adiciona barra deslizante."""
    labels = list(labels)
    values = list(values)
    n = len(labels)
    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        text=[f"{v:{fmt}}" for v in values],
        textposition="inside",
        textfont=dict(size=14, color="white"),
        marker_color=cor,
    ))
    xaxis_cfg = dict(tickfont=dict(size=13))
    h_extra = 0
    if max_show and n > max_show:
        xaxis_cfg["range"]       = [-0.5, max_show - 0.5]
        xaxis_cfg["rangeslider"] = dict(visible=True, thickness=0.06)
        h_extra = 45
    fig.update_layout(
        height=height + h_extra,
        bargap=0.5,
        margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
        xaxis=xaxis_cfg,
    )
    return fig


def extrair_dados_pdf(pdf_file) -> list[dict]:
    """
    Suporta dois formatos de relatório:
      • Formato diário  (RPCP620 antigo): Usuário → Turno → Dia → Recurso → Itens
      • Formato período (RPCP621 novo):   Recurso → Turno → Funcionário → Itens
    """
    # Termos que indicam linhas de totais/cabeçalho que devem ser ignoradas.
    # IMPORTANTE: nunca colocar nomes de máquinas aqui — a linha "Recurso: X - NOME"
    # contém o nome da máquina e seria descartada inteira, perdendo todos os itens.
    _SKIP = (
        "Total de Registros", "Total Funcion", "Total Turno", "Total Recurso",
        "Total Dia", "Total Geral",
        "Item QuantidadeUnidade", "POLIMPRESS", "CNPJ", "DRACENA",
        "Primeira Quebra", "Segunda Quebra", "Terceira Quebra", "Agrupamento",
        "Tipo Recurso", "Local Busca", "ROTINA:", "HORA:", "PÁGINA:", "INSCR.",
        "Análise Resumida", "Análise De",
    )
    # Prefixos que NUNCA devem ser descartados pelo _SKIP (são cabeçalhos de dados)
    _SAFE_PREFIXES = ("Recurso:", "Funcionário:", "Funcionario:", "Usuario:", "Usuário:")

    dados = []
    with pdfplumber.open(pdf_file) as pdf:
        operador = turno = recurso_atual = None
        data_atual = None
        periodo_inicio_str = periodo_fim_str = None
        formato_novo = False   # True = RPCP621 (Funcionário/Período)

        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto:
                continue
            for linha in texto.split("\n"):
                linha = linha.strip()
                if not linha:
                    continue
                # Descarta linhas de totais/cabeçalhos, MAS nunca descarta
                # linhas que começam com prefixos de dados (Recurso, Funcionário, etc.)
                if any(k in linha for k in _SKIP) and not any(linha.startswith(p) for p in _SAFE_PREFIXES):
                    continue

                # ── Período (novo formato) ──────────────────────────────────
                m = _RE_PERIODO.search(linha)
                if m:
                    periodo_inicio_str = m.group(1)
                    periodo_fim_str    = m.group(2)
                    if m.group(3):          # Turno*:X no cabeçalho
                        turno = f"Turno {m.group(3)}"
                    continue

                # ── Funcionário (novo formato) ──────────────────────────────
                m = _RE_FUNCIONARIO.match(linha)
                if m:
                    nome = re.split(r"\s+Tipo\s+", m.group(1))[0].strip()
                    operador = nome
                    formato_novo = True
                    continue

                # ── Usuário (formato antigo) ────────────────────────────────
                m = _RE_USUARIO.search(linha)
                if m:
                    nome = re.split(r"\s+Tipo\s+", m.group(1))[0].strip()
                    operador = nome
                    recurso_atual = None   # antigo: máquina vem depois do operador
                    continue

                # ── Turno ───────────────────────────────────────────────────
                if linha.startswith("Turno:"):
                    m = _RE_TURNO.search(linha)
                    if m:
                        turno = f"Turno {m.group(1)}"
                    continue

                # ── Dia (formato antigo) ────────────────────────────────────
                m = _RE_DIA.search(linha)
                if m:
                    data_atual = m.group(1)
                    continue

                # ── Recurso / Máquina ───────────────────────────────────────
                if "Recurso:" in linha and "Tipo Recurso:" not in linha and "Total" not in linha:
                    m = _RE_RECURSO.search(linha)
                    if m:
                        recurso_atual = m.group(1).strip()
                    if formato_novo:
                        operador = None   # novo: troca de máquina reseta operador
                    continue

                # ── Itens ───────────────────────────────────────────────────
                if not operador or not recurso_atual:
                    continue
                if "Total" in linha or "Registros" in linha:
                    continue

                # Referência de data + detecção do formato
                if data_atual:
                    data_ref     = data_atual
                    formato_dado = "diario"    # tem Dia: → dados por dia
                elif periodo_fim_str:
                    data_ref     = periodo_fim_str
                    formato_dado = "quinzenal" # sem Dia: → agregado por período
                else:
                    continue

                m = _RE_ITEM_GERAL.match(linha)
                if not m:
                    continue

                unidade = m.group(4)   # UN, KG, MT, etc.
                try:
                    quantidade = _parse_br_float(m.group(3))
                    peso       = _parse_br_float(m.group(5))
                except ValueError:
                    continue

                dados.append({
                    "Operador":          operador,
                    "Nome Curto":        nome_curto(operador),
                    "Turno":             turno or "Não Informado",
                    "Data":              data_ref,
                    "Formato":           formato_dado,
                    "Periodo_Inicio":    periodo_inicio_str,
                    "Periodo_Fim":       periodo_fim_str,
                    "Máquina":           recurso_atual,
                    "Cód Item":          m.group(1).strip(),
                    "Descrição Item":    m.group(2).strip(),
                    "Unidade":           unidade,
                    "Qtd (UN)":          quantidade,
                    "Peso (KG)":         peso,
                    "Peso Médio/UN (g)": (peso / quantidade * 1000) if quantidade > 0 else 0,
                })
    return dados


# ── Upload ───────────────────────────────────────────────────────────────────
arquivos_pdf = st.file_uploader(
    "Arraste e solte os PDFs de produção aqui (pode ser 1 por turno ou todos juntos)",
    type=["pdf"],
    accept_multiple_files=True,
)

if not arquivos_pdf:
    st.info("⬆️ Carregue pelo menos um PDF para iniciar a análise.")
    st.stop()

todos_dados: list[dict] = []
erros: list[str] = []
for arquivo in arquivos_pdf:
    try:
        todos_dados.extend(extrair_dados_pdf(arquivo))
    except Exception as e:
        erros.append(f"{arquivo.name}: {e}")

for msg in erros:
    st.error(f"Erro ao processar — {msg}")

if not todos_dados:
    st.warning("Nenhum dado extraído. Verifique se o layout do PDF corresponde ao esperado.")
    st.stop()

df = pd.DataFrame(todos_dados)
df["Data_dt"]           = pd.to_datetime(df["Data"], format="%d/%m/%Y")
df["Periodo_Inicio_dt"] = pd.to_datetime(df["Periodo_Inicio"], format="%d/%m/%Y", errors="coerce")
df["Periodo_Fim_dt"]    = pd.to_datetime(df["Periodo_Fim"],    format="%d/%m/%Y", errors="coerce")

# ── Turno principal por operador ──────────────────────────────────────────────
# Para cada operador, o turno com maior KG produzido é o seu turno "oficial".
# Horas extras em outros turnos ficam registradas sob o turno principal.
_turno_principal = (
    df.groupby(["Operador", "Turno"])["Peso (KG)"].sum()
    .reset_index()
    .sort_values("Peso (KG)", ascending=False)
    .drop_duplicates(subset="Operador")   # mantém só o turno com mais KG
    .set_index("Operador")["Turno"]
)
df["Turno"] = df["Operador"].map(_turno_principal)

_turnos_no_df = sorted(df["Turno"].unique())
_resumo_turnos = "  ·  ".join(
    f"**{t}**: {(df['Turno']==t).sum()} reg." for t in _turnos_no_df
)
st.success(
    f"✅ {len(arquivos_pdf)} PDF(s) · {len(df)} registros · "
    f"{df['Operador'].nunique()} operador(es) · "
    f"{df['Máquina'].nunique()} máquina(s)"
)
if _resumo_turnos:
    st.caption(_resumo_turnos)

aba1, aba2, aba3, aba4, aba9, aba5, aba6, aba7, aba8 = st.tabs([
    "🏆 Ranking de Operadores",
    "🏭 Ranking por Máquina",
    "⚖️ Comparativo por Item",
    "🏅 Top Produção",
    "🎯 Comparativo por Máquina",
    "📈 Evolução Operador",
    "📅 KG / Dia",
    "📊 Resumo Geral",
    "📋 Dados Brutos",
])


# ── helpers de resumo ────────────────────────────────────────────────────────
def calcular_resumo_operadores(df_in: pd.DataFrame) -> pd.DataFrame:
    horas_op = {}
    dias_op  = {}
    for op, g in df_in.groupby("Operador"):
        ts      = g["Turno"].iloc[0]
        fmt     = g["Formato"].iloc[0]
        p_ini   = g["Periodo_Inicio_dt"].iloc[0]
        p_fim   = g["Periodo_Fim_dt"].iloc[0]
        if fmt == "quinzenal" and pd.notna(p_ini) and pd.notna(p_fim):
            # Sem Dia: → agrega pelo período completo
            horas_op[op] = horas_operador_periodo(ts, p_ini, p_fim)
            dias_op[op]  = dias_trabalhados_no_periodo(p_ini, p_fim)
        else:
            # Com Dia: → soma horas por dia trabalhado (mais preciso)
            dias_uniq    = g["Data_dt"].dropna().drop_duplicates()
            horas_op[op] = dias_uniq.apply(lambda d: horas_turno(ts, d)).sum()
            dias_op[op]  = len(dias_uniq)

    turno_por_op  = df_in.groupby("Operador")["Turno"].first()
    nome_curto_op = df_in.groupby("Operador")["Nome Curto"].first()

    # Total_UN: apenas itens com unidade UN (não mistura metros, KG de matéria, etc.)
    _qtd_un_por_op = (
        df_in[df_in["Unidade"] == "UN"]
        .groupby("Operador")["Qtd (UN)"].sum()
    )
    r = (
        df_in.groupby("Operador")
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Apontamentos=("Cód Item", "count"),
            Produtos_Distintos=("Cód Item", "nunique"),
            Peso_Medio_g=("Peso Médio/UN (g)", "mean"),
        )
        .join(turno_por_op).join(nome_curto_op)
        .reset_index()
    )
    r["Total_UN"]            = r["Operador"].map(_qtd_un_por_op).fillna(0).astype(int)
    r["Dias Trabalhados"]    = r["Operador"].map(dias_op)
    r["Horas Trabalhadas"]   = r["Operador"].map(horas_op).round(1)
    r["KG / Hora"]           = (r["Total_KG"] / r["Horas Trabalhadas"]).round(2)
    r["UN / Hora"]           = (r["Total_UN"] / r["Horas Trabalhadas"]).round(1)
    r["KG / Dia"]            = (r["Total_KG"] / r["Dias Trabalhados"]).round(1)
    r["Apontamentos / Dia"]  = (r["Apontamentos"] / r["Dias Trabalhados"]).round(1)
    r["Peso Médio/peça (g)"] = r["Peso_Medio_g"].round(2)
    return r.sort_values("KG / Hora", ascending=False).reset_index(drop=True)


# ── Pré-cálculos globais (reutilizados em múltiplas abas) ────────────────────
resumo = calcular_resumo_operadores(df)

# Resumo de máquinas (global)
_df_un_g = df[df["Unidade"] == "UN"].groupby(["Máquina", "Nome Curto", "Turno"])["Qtd (UN)"].sum().rename("Total_UN")
_df_maq_op_dia_g = (
    df.groupby(["Máquina", "Nome Curto", "Turno"])
    .agg(Total_KG=("Peso (KG)", "sum"), Dias=("Data", "nunique"))
    .reset_index()
    .join(_df_un_g, on=["Máquina", "Nome Curto", "Turno"])
)
_df_maq_op_dia_g["Total_UN"] = _df_maq_op_dia_g["Total_UN"].fillna(0).astype(int)
_df_maq_op_dia_g["KG / Dia (op)"] = (_df_maq_op_dia_g["Total_KG"] / _df_maq_op_dia_g["Dias"]).round(1)

_melhor_por_maq_g = (
    _df_maq_op_dia_g.sort_values("KG / Dia (op)", ascending=False)
    .drop_duplicates("Máquina")
    .set_index("Máquina")[["Nome Curto", "KG / Dia (op)"]]
    .rename(columns={"Nome Curto": "Melhor Op", "KG / Dia (op)": "Melhor KG/Dia"})
)

_df_un_maq_g = df[df["Unidade"] == "UN"].groupby("Máquina")["Qtd (UN)"].sum().rename("Total_UN")
df_maq_resumo_g = (
    df.groupby("Máquina")
    .agg(
        Total_KG=("Peso (KG)", "sum"),
        Dias_Ativas=("Data", "nunique"),
        Apontamentos=("Cód Item", "count"),
        Operadores=("Nome Curto", lambda x: ", ".join(sorted(x.unique()))),
    )
    .reset_index()
    .join(_df_un_maq_g, on="Máquina")
    .join(_melhor_por_maq_g, on="Máquina")
)
df_maq_resumo_g["Total_UN"] = df_maq_resumo_g["Total_UN"].fillna(0).astype(int)
df_maq_resumo_g["KG / Dia"] = (df_maq_resumo_g["Total_KG"] / df_maq_resumo_g["Dias_Ativas"]).round(1)
df_maq_resumo_g["UN / Dia"] = (df_maq_resumo_g["Total_UN"] / df_maq_resumo_g["Dias_Ativas"]).round(0)
df_maq_resumo_g = df_maq_resumo_g.sort_values("KG / Dia", ascending=False).reset_index(drop=True)
df_maq_resumo_g["Máquina Curta"] = (
    df_maq_resumo_g["Máquina"].str.extract(r"^(\d+)")[0].fillna("")
    + " - "
    + df_maq_resumo_g["Máquina"].str.split(" - ").str[1:].str.join(" ").str[:22]
)
df_maq_resumo_g["Nº Máquina"] = df_maq_resumo_g["Máquina"].str.extract(r"^(\d+)")[0].fillna(df_maq_resumo_g["Máquina"])


# ── Aba 1: Ranking de Operadores ─────────────────────────────────────────────
with aba1:
    st.header("Ranking de Operadores — Métricas Normalizadas")

    with st.expander("ℹ️ Por que usar KG/hora e não KG/dia?"):
        st.markdown("""
        Comparar totais brutos é injusto quando os operadores têm turnos diferentes e períodos diferentes.

        | Turno | Semana | Sábado |
        |-------|--------|--------|
        | 1 (05:20–13:40) | **7h50** | **4h50** |
        | 2 (13:40–22:00) | **7h50** | **4h50** |
        | 3 (22:00–05:20) | **6h50** | **4h20** |
        """)

    # Cards — top 3 visíveis, demais em expander
    medalhas = ["🥇", "🥈", "🥉"]
    _TOP3 = min(len(resumo), 3)
    cols = st.columns(_TOP3)
    for i in range(_TOP3):
        row = resumo.iloc[i]
        with cols[i]:
            st.metric(
                label=f"{medalhas[i]} {row['Nome Curto']}",
                value=f"{row['KG / Hora']:.2f} KG/hora",
                delta=f"{row['Turno']} · {row['Dias Trabalhados']} dias · {row['Horas Trabalhadas']:.0f}h",
            )
    if len(resumo) > 3:
        with st.expander(f"Ver todos os {len(resumo)} operadores"):
            _rest = resumo.iloc[3:]
            _cols2 = st.columns(min(len(_rest), 5))
            for j, (_, row) in enumerate(_rest.iterrows()):
                with _cols2[j % 5]:
                    st.metric(
                        label=row["Nome Curto"],
                        value=f"{row['KG / Hora']:.2f} KG/hora",
                        delta=f"{row['Turno']} · {row['Dias Trabalhados']} dias",
                    )

    quebra_pagina()

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("KG / Hora trabalhada (métrica principal)")
        st.caption("Elimina diferença de turno, sábados e dias trabalhados. ← arraste a barra para ver todos →")
        st.plotly_chart(
            bar_chart(resumo["Nome Curto"], resumo["KG / Hora"], fmt=".2f", max_show=10),
            use_container_width=True,
        )

    with col_b:
        st.subheader("UN / Hora trabalhada")
        st.caption("UN alto com KG baixo = produtos leves — compare sempre com Peso Médio/peça.")
        st.plotly_chart(
            bar_chart(resumo["Nome Curto"], resumo["UN / Hora"], fmt=".0f", cor="#5CB85C", max_show=10),
            use_container_width=True,
        )

    quebra_pagina()
    st.subheader("Peso médio por peça (proxy de complexidade)")
    st.caption("Sacos mais pesados são maiores/mais espessos — máquina roda mais devagar.")
    st.plotly_chart(
        bar_chart(resumo["Nome Curto"], resumo["Peso Médio/peça (g)"], fmt=".1f", cor="#F0AD4E", max_show=10),
        use_container_width=True,
    )

    quebra_pagina()
    st.subheader("Resumo completo")
    st.dataframe(
        resumo[[
            "Nome Curto", "Turno", "Dias Trabalhados", "Horas Trabalhadas",
            "KG / Hora", "UN / Hora", "KG / Dia",
            "Peso Médio/peça (g)", "Produtos_Distintos", "Total_KG", "Total_UN",
        ]].rename(columns={
            "Produtos_Distintos": "Produtos Distintos",
            "Total_KG": "Total KG",
            "Total_UN": "Total UN",
        }),
        use_container_width=True, hide_index=True,
    )

    quebra_pagina()
    st.subheader("KG/hora por operador — detalhado por máquina")
    st.caption("Abas ordenadas da máquina que mais produziu para a que menos produziu.")

    def resumo_por_maquina(df_m: pd.DataFrame) -> pd.DataFrame:
        horas_op = {}
        dias_op  = {}
        for op, g in df_m.groupby("Operador"):
            ts    = g["Turno"].iloc[0]
            fmt   = g["Formato"].iloc[0]
            p_ini = g["Periodo_Inicio_dt"].iloc[0]
            p_fim = g["Periodo_Fim_dt"].iloc[0]
            if fmt == "quinzenal" and pd.notna(p_ini) and pd.notna(p_fim):
                horas_op[op] = horas_operador_periodo(ts, p_ini, p_fim)
                dias_op[op]  = dias_trabalhados_no_periodo(p_ini, p_fim)
            else:
                dias_uniq    = g["Data_dt"].dropna().drop_duplicates()
                horas_op[op] = dias_uniq.apply(lambda d: horas_turno(ts, d)).sum()
                dias_op[op]  = len(dias_uniq)
        r = (
            df_m.groupby(["Operador", "Nome Curto", "Turno"])
            .agg(Total_KG=("Peso (KG)", "sum"))
            .reset_index()
        )
        r["Dias"]      = r["Operador"].map(dias_op)
        r["Horas"]     = r["Operador"].map(horas_op).round(1)
        r["KG / Hora"] = (r["Total_KG"] / r["Horas"]).round(2)
        r["KG / Dia"]  = (r["Total_KG"] / r["Dias"]).round(1)
        return r.sort_values("KG / Hora", ascending=False).reset_index(drop=True)

    # Ordena máquinas por KG total produzido (desc)
    maquinas_ord = (
        df.groupby("Máquina")["Peso (KG)"].sum()
        .sort_values(ascending=False)
        .index.tolist()
    )

    def _label_tab(m: str) -> str:
        partes = m.split(" - ")
        num = partes[0].strip()
        nome = partes[1].strip()[:14] if len(partes) > 1 else ""
        return f"{num} · {nome}" if nome else num

    tabs_maq = st.tabs([_label_tab(m) for m in maquinas_ord])
    _medalhas = ["🥇", "🥈", "🥉"]

    for tab_m, maq in zip(tabs_maq, maquinas_ord):
        with tab_m:
            df_m = df[df["Máquina"] == maq]
            r_m = resumo_por_maquina(df_m)
            n_m = len(r_m)

            # Melhor por turno nos cards principais
            _cards_m = r_m.drop_duplicates(subset="Turno").reset_index(drop=True)
            _rest_m  = r_m.drop(index=r_m.drop_duplicates(subset="Turno").index).reset_index(drop=True)
            cols_m = st.columns(max(len(_cards_m), 1))
            for i, (_, row) in enumerate(_cards_m.iterrows()):
                _med_m = _medalhas[i] if i < len(_medalhas) else "·"
                with cols_m[i]:
                    st.metric(
                        label=f"{_med_m} {row['Nome Curto']} · {row['Turno']}",
                        value=f"{row['KG / Hora']:.2f} KG/hora",
                        delta=f"{row['Dias']} dias · {row['Horas']:.0f}h",
                    )
            if not _rest_m.empty:
                with st.expander(f"Ver demais {len(_rest_m)} operador(es)"):
                    _cols_rest = st.columns(min(len(_rest_m), 5))
                    for j, (_, row) in enumerate(_rest_m.iterrows()):
                        with _cols_rest[j % 5]:
                            st.metric(
                                label=f"{row['Nome Curto']} · {row['Turno']}",
                                value=f"{row['KG / Hora']:.2f} KG/hora",
                                delta=f"{row['Dias']} dias",
                            )

            st.plotly_chart(
                bar_chart(r_m["Nome Curto"], r_m["KG / Hora"], fmt=".2f", max_show=5),
                use_container_width=True,
            )


# ── Aba 2: Ranking por Máquina ───────────────────────────────────────────────
with aba2:
    st.header("Ranking por Máquina — KG produzidos")
    st.caption("Mesma lógica do ranking de operadores, agora agrupando por recurso/máquina.")

    # KG/dia por operador dentro de cada máquina (para achar o melhor)
    df_maq_op_dia = (
        df.groupby(["Máquina", "Nome Curto", "Turno"])
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Total_UN=("Qtd (UN)", "sum"),
            Dias=("Data", "nunique"),
        )
        .reset_index()
    )
    df_maq_op_dia["KG / Dia (op)"] = (df_maq_op_dia["Total_KG"] / df_maq_op_dia["Dias"]).round(1)

    # Melhor operador por máquina (maior KG/Dia)
    melhor_por_maq = (
        df_maq_op_dia.sort_values("KG / Dia (op)", ascending=False)
        .drop_duplicates("Máquina")
        .set_index("Máquina")[["Nome Curto", "KG / Dia (op)"]]
        .rename(columns={"Nome Curto": "Melhor Op", "KG / Dia (op)": "Melhor KG/Dia"})
    )

    df_maq_resumo = (
        df.groupby("Máquina")
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Total_UN=("Qtd (UN)", "sum"),
            Dias_Ativas=("Data", "nunique"),
            Apontamentos=("Cód Item", "count"),
            Operadores=("Nome Curto", lambda x: ", ".join(sorted(x.unique()))),
        )
        .reset_index()
        .join(melhor_por_maq, on="Máquina")
    )
    df_maq_resumo["KG / Dia"] = (df_maq_resumo["Total_KG"] / df_maq_resumo["Dias_Ativas"]).round(1)
    df_maq_resumo["UN / Dia"] = (df_maq_resumo["Total_UN"] / df_maq_resumo["Dias_Ativas"]).round(0)
    df_maq_resumo = df_maq_resumo.sort_values("KG / Dia", ascending=False).reset_index(drop=True)

    df_maq_resumo["Máquina Curta"] = (
        df_maq_resumo["Máquina"].str.extract(r"^(\d+)")[0].fillna("")
        + " - "
        + df_maq_resumo["Máquina"].str.split(" - ").str[1:].str.join(" ").str[:22]
    )

    # Cards com melhor operador — top 3 visíveis, demais em expander
    medalhas_m = ["🥇", "🥈", "🥉"]
    top_n = min(len(df_maq_resumo), 3)
    cols_m = st.columns(top_n)
    for i in range(top_n):
        row = df_maq_resumo.iloc[i]
        with cols_m[i]:
            st.metric(
                label=f"{medalhas_m[i]} Máquina {row['Máquina'].split(' - ')[0]}",
                value=f"{row['KG / Dia']:.0f} KG/dia",
                delta=f"{row['Dias_Ativas']} dias ativos",
            )
            st.caption(f"🏅 Melhor: **{row['Melhor Op']}** ({row['Melhor KG/Dia']:.0f} KG/dia)")
    if len(df_maq_resumo) > 3:
        with st.expander(f"Ver todas as {len(df_maq_resumo)} máquinas"):
            _rest_maq = df_maq_resumo.iloc[3:]
            _cols_maq = st.columns(min(len(_rest_maq), 4))
            for j, (_, row) in enumerate(_rest_maq.iterrows()):
                with _cols_maq[j % 4]:
                    st.metric(
                        label=f"Máquina {row['Máquina'].split(' - ')[0]}",
                        value=f"{row['KG / Dia']:.0f} KG/dia",
                        delta=f"{row['Dias_Ativas']} dias ativos",
                    )
                    st.caption(f"🏅 Melhor: **{row['Melhor Op']}** ({row['Melhor KG/Dia']:.0f} KG/dia)")

    quebra_pagina()

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("KG / Dia por Máquina")
        st.plotly_chart(
            bar_chart(df_maq_resumo["Máquina Curta"], df_maq_resumo["KG / Dia"], fmt=".0f", max_show=5),
            use_container_width=True,
        )
    with col_b:
        st.subheader("UN / Dia por Máquina")
        st.plotly_chart(
            bar_chart(df_maq_resumo["Máquina Curta"], df_maq_resumo["UN / Dia"], fmt=".0f", cor="#5CB85C", max_show=5),
            use_container_width=True,
        )

    quebra_pagina()
    st.subheader("Melhor operador em cada máquina — KG/dia")
    st.caption("Comparativo direto: para cada máquina, qual operador foi mais produtivo (KG/dia naquela máquina).")

    cores = ["#4C9BE8", "#5CB85C", "#F0AD4E", "#D9534F"]
    todas_maquinas = sorted(df["Máquina"].unique())
    maq_sel_comp = st.selectbox(
        "Selecione uma máquina para ver o comparativo de operadores:",
        todas_maquinas,
        format_func=lambda m: m[:60],
    )

    df_comp = df_maq_op_dia[df_maq_op_dia["Máquina"] == maq_sel_comp].sort_values("KG / Dia (op)", ascending=False)
    n_comp  = len(df_comp)
    col_g, col_t = st.columns([1, 1])
    with col_g:
        xaxis_comp = dict(tickfont=dict(size=13))
        h_comp = 300
        if n_comp > 5:
            xaxis_comp["range"]       = [-0.5, 4.5]
            xaxis_comp["rangeslider"] = dict(visible=True, thickness=0.06)
            h_comp = 345
        fig_comp = go.Figure(go.Bar(
            x=df_comp["Nome Curto"],
            y=df_comp["KG / Dia (op)"],
            text=df_comp["KG / Dia (op)"].apply(lambda v: f"{v:.0f}"),
            textposition="inside",
            textfont=dict(size=15, color="white"),
            marker_color=[cores[i % len(cores)] for i in range(n_comp)],
        ))
        fig_comp.update_layout(
            height=h_comp, bargap=0.5, margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
            xaxis=xaxis_comp,
        )
        st.plotly_chart(fig_comp, use_container_width=True)

    with col_t:
        st.dataframe(
            df_comp[["Nome Curto", "Turno", "Dias", "Total_KG", "Total_UN", "KG / Dia (op)"]]
            .rename(columns={
                "Nome Curto": "Operador",
                "Dias": "Dias na Máq.",
                "Total_KG": "Total KG",
                "Total_UN": "Total UN",
                "KG / Dia (op)": "KG / Dia",
            }),
            use_container_width=True, hide_index=True,
        )

    quebra_pagina()
    st.subheader("KG total por Máquina × Operador")
    df_maq_op_grp = df.groupby(["Máquina", "Nome Curto"])["Peso (KG)"].sum().reset_index()
    pivot = df_maq_op_grp.pivot(index="Máquina", columns="Nome Curto", values="Peso (KG)").fillna(0)
    fig_stack = go.Figure()
    for i, op in enumerate(pivot.columns):
        fig_stack.add_trace(go.Bar(
            name=op, x=pivot.index, y=pivot[op],
            text=pivot[op].apply(lambda v: f"{v:,.0f}" if v > 0 else ""),
            textposition="inside", textfont=dict(size=11, color="white"),
            marker_color=cores[i % len(cores)],
        ))
    fig_stack.update_layout(
        barmode="stack", height=380,
        margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
    )
    st.plotly_chart(fig_stack, use_container_width=True)

    quebra_pagina()
    st.subheader("Tabela detalhada por máquina")
    st.dataframe(
        df_maq_resumo[[
            "Máquina", "KG / Dia", "UN / Dia", "Dias_Ativas",
            "Apontamentos", "Total_KG", "Total_UN", "Melhor Op", "Melhor KG/Dia", "Operadores",
        ]].rename(columns={
            "Dias_Ativas": "Dias Ativas",
            "Total_KG": "Total KG",
            "Total_UN": "Total UN",
            "Melhor Op": "Melhor Operador",
            "Melhor KG/Dia": "Melhor KG/Dia",
        }),
        use_container_width=True, hide_index=True,
    )


# ── Aba 3: Comparativo por Item ───────────────────────────────────────────────
with aba3:
    st.header("Comparativo por Item — Quem foi Melhor?")
    st.caption("Selecione um produto e veja o desempenho de cada operador nele — comparação justa, mesmo produto.")

    desc_map = df.drop_duplicates("Cód Item").set_index("Cód Item")["Descrição Item"].to_dict()
    ops_por_item = df.groupby("Cód Item")["Operador"].nunique()

    # Horas reais por (item, operador) — período ou dias únicos conforme o formato
    horas_item_op: dict[tuple, float] = {}
    for (item, op), g in df.groupby(["Cód Item", "Operador"]):
        ts    = g["Turno"].iloc[0]
        fmt   = g["Formato"].iloc[0]
        p_ini = g["Periodo_Inicio_dt"].iloc[0]
        p_fim = g["Periodo_Fim_dt"].iloc[0]
        if fmt == "quinzenal" and pd.notna(p_ini) and pd.notna(p_fim):
            horas_item_op[(item, op)] = horas_operador_periodo(ts, p_ini, p_fim)
        else:
            dias_uniq = g["Data_dt"].dropna().drop_duplicates()
            horas_item_op[(item, op)] = dias_uniq.apply(lambda d: horas_turno(ts, d)).sum()

    # KG/hora e UN/apontamento por operador × item
    df_item_op = (
        df.groupby(["Cód Item", "Operador", "Nome Curto", "Turno"])
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Total_UN=("Qtd (UN)", "sum"),
            Dias=("Data", "nunique"),
            Apontamentos=("Data", "count"),
        )
        .reset_index()
    )
    df_item_op["Horas"] = df_item_op.apply(
        lambda r: horas_item_op.get((r["Cód Item"], r["Operador"]), None), axis=1
    )
    df_item_op["KG / Hora"] = (df_item_op["Total_KG"] / df_item_op["Horas"]).round(2)
    df_item_op["UN / Apontamento"] = (df_item_op["Total_UN"] / df_item_op["Apontamentos"]).round(0)

    # Melhor operador por item (maior KG/Hora)
    melhor_por_item = (
        df_item_op.sort_values("KG / Hora", ascending=False)
        .drop_duplicates("Cód Item")
        .set_index("Cód Item")[["Nome Curto", "KG / Hora"]]
        .rename(columns={"Nome Curto": "Melhor Op", "KG / Hora": "Melhor KG/Hora"})
    )

    # Itens com mais de 1 operador
    itens_comparaveis = ops_por_item[ops_por_item > 1].index.tolist()

    # Selectbox — todos os itens, ordenado por nº de operadores desc, depois por código
    todos_itens = (
        ops_por_item
        .sort_values(ascending=False)
        .index.tolist()
    )
    def label_item(cod):
        compartilhado = "👥 " if cod in itens_comparaveis else "👤 "
        return f"{compartilhado}{cod} — {desc_map[cod][:55]}"

    item_sel = st.selectbox(
        "Selecione o produto (👥 = fabricado por múltiplos operadores):",
        todos_itens,
        format_func=label_item,
    )

    df_comp_item = df_item_op[df_item_op["Cód Item"] == item_sel].sort_values("KG / Hora", ascending=False)
    n_ops = len(df_comp_item)

    st.info(f"**{item_sel}** · {desc_map[item_sel]}")

    # ── Top 3 com medalhas ───────────────────────────────────────────────────
    _med_item = ["🥇", "🥈", "🥉"]
    _top3_item = min(n_ops, 3)
    _cols_item = st.columns(_top3_item)
    for i in range(_top3_item):
        row = df_comp_item.iloc[i]
        with _cols_item[i]:
            st.metric(
                label=f"{_med_item[i]} {row['Nome Curto']}",
                value=f"{row['KG / Hora']:.2f} KG/hora",
                delta=f"{row['Turno']} · {row['Dias']} dias · {row['Horas']:.0f}h",
            )
    if n_ops > 3:
        with st.expander(f"Ver todos os {n_ops} operadores"):
            _rest_item = df_comp_item.iloc[3:]
            _cols_rest_item = st.columns(min(len(_rest_item), 5))
            for j, (_, row) in enumerate(_rest_item.iterrows()):
                with _cols_rest_item[j % 5]:
                    st.metric(
                        label=row["Nome Curto"],
                        value=f"{row['KG / Hora']:.2f} KG/hora",
                        delta=f"{row['Dias']} dias",
                    )

    cores = ["#4C9BE8", "#5CB85C", "#F0AD4E", "#D9534F"]

    col_g, col_t = st.columns([1, 1])
    with col_g:
        st.subheader("KG / Hora neste produto")
        fig_kg = go.Figure(go.Bar(
            x=df_comp_item["Nome Curto"],
            y=df_comp_item["KG / Hora"],
            text=df_comp_item["KG / Hora"].apply(lambda v: f"{v:.2f}"),
            textposition="inside",
            textfont=dict(size=15, color="white"),
            marker_color=[cores[i % len(cores)] for i in range(n_ops)],
        ))
        fig_kg.update_layout(
            height=300, bargap=0.5, margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
        )
        st.plotly_chart(fig_kg, use_container_width=True)

    with col_t:
        st.subheader("UN / Apontamento (velocidade)")
        fig_un = go.Figure(go.Bar(
            x=df_comp_item["Nome Curto"],
            y=df_comp_item["UN / Apontamento"],
            text=df_comp_item["UN / Apontamento"].apply(lambda v: f"{v:.0f}"),
            textposition="inside",
            textfont=dict(size=15, color="white"),
            marker_color=[cores[i % len(cores)] for i in range(n_ops)],
        ))
        fig_un.update_layout(
            height=300, bargap=0.5, margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
        )
        st.plotly_chart(fig_un, use_container_width=True)

    st.dataframe(
        df_comp_item[[
            "Nome Curto", "Turno", "Dias", "Horas", "Apontamentos",
            "Total_KG", "Total_UN", "KG / Hora", "UN / Apontamento",
        ]].rename(columns={
            "Nome Curto": "Operador",
            "Dias": "Dias prod.",
            "Horas": "Horas prod.",
            "Total_KG": "Total KG",
            "Total_UN": "Total UN",
        }),
        use_container_width=True, hide_index=True,
    )

    if n_ops > 1:
        quebra_pagina()
        st.subheader("Evolução diária neste produto")
        df_evol = (
            df[df["Cód Item"] == item_sel]
            .groupby(["Data_dt", "Nome Curto"])["Qtd (UN)"]
            .sum().reset_index()
            .pivot(index="Data_dt", columns="Nome Curto", values="Qtd (UN)")
            .fillna(0).sort_index()
        )
        st.line_chart(df_evol)

    quebra_pagina()
    st.subheader("Ranking geral: melhor operador por produto")
    st.caption("Todos os produtos que foram fabricados por mais de um operador.")
    if itens_comparaveis:
        df_shared_base = df_item_op[df_item_op["Cód Item"].isin(itens_comparaveis)]
        resumo_shared = (
            df_shared_base.groupby("Cód Item")
            .apply(lambda g: pd.Series({
                "Descrição": desc_map.get(g.name, ""),
                "Melhor Operador": g.loc[g["KG / Hora"].idxmax(), "Nome Curto"],
                "Melhor KG/Hora": g["KG / Hora"].max().round(2),
                "Operadores": ", ".join(sorted(g["Nome Curto"].unique())),
                "Nº Operadores": g["Nome Curto"].nunique(),
            }), include_groups=False)
            .reset_index()
            .sort_values("Melhor KG/Hora", ascending=False)
        )
        st.dataframe(resumo_shared, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum produto compartilhado entre operadores nos PDFs carregados.")


# ── Aba 4: Top Produção ───────────────────────────────────────────────────────
with aba4:
    st.header("🏅 Top Produção — Ranking por Volume Total")
    st.caption("Quem e quais máquinas produziram mais em quantidade absoluta (KG e UN) no período analisado.")

    _med5 = ["🥇", "🥈", "🥉"]

    def _cards_expander(df_rank, label_col, value_col, value_fmt, value_suffix, expander_label, delta_col=None, delta_fmt=None, delta_suffix=""):
        """Renderiza top-3 cards + expander para o restante."""
        n = len(df_rank)
        top3 = min(n, 3)
        cols = st.columns(top3)
        for i in range(top3):
            row = df_rank.iloc[i]
            val_str = f"{row[value_col]:{value_fmt}}{value_suffix}"
            dlt_str = None
            if delta_col and delta_fmt:
                dlt_str = f"{row[delta_col]:{delta_fmt}}{delta_suffix}"
            with cols[i]:
                st.metric(
                    label=f"{_med5[i]} {row[label_col]}",
                    value=val_str,
                    delta=dlt_str,
                )
        if n > 3:
            with st.expander(expander_label):
                rest = df_rank.iloc[3:]
                cols2 = st.columns(min(len(rest), 5))
                for j, (_, row) in enumerate(rest.iterrows()):
                    val_str = f"{row[value_col]:{value_fmt}}{value_suffix}"
                    dlt_str = None
                    if delta_col and delta_fmt:
                        dlt_str = f"{row[delta_col]:{delta_fmt}}{delta_suffix}"
                    with cols2[j % 5]:
                        st.metric(
                            label=row[label_col],
                            value=val_str,
                            delta=dlt_str,
                        )

    # ── Operadores ────────────────────────────────────────────────────────────
    st.subheader("👷 Ranking de Operadores")
    op_totais = (
        df.groupby(["Operador", "Nome Curto"])
        .agg(Total_KG=("Peso (KG)", "sum"), Total_UN=("Qtd (UN)", "sum"))
        .reset_index()
    )

    col_op1, col_op2 = st.columns(2)

    with col_op1:
        st.markdown("#### 📦 Total de KG produzido")
        op_kg = op_totais.sort_values("Total_KG", ascending=False).reset_index(drop=True)
        _cards_expander(
            op_kg,
            label_col="Nome Curto",
            value_col="Total_KG",
            value_fmt=",.0f",
            value_suffix=" KG",
            expander_label=f"Ver todos os {len(op_kg)} operadores (KG)",
            delta_col="Total_UN",
            delta_fmt=",.0f",
            delta_suffix=" UN",
        )
        st.plotly_chart(
            bar_chart(op_kg["Nome Curto"], op_kg["Total_KG"], fmt=",.0f", cor="#4C9BE8", max_show=10),
            use_container_width=True,
        )

    with col_op2:
        st.markdown("#### 🔢 Total de UN produzido")
        op_un = op_totais.sort_values("Total_UN", ascending=False).reset_index(drop=True)
        _cards_expander(
            op_un,
            label_col="Nome Curto",
            value_col="Total_UN",
            value_fmt=",.0f",
            value_suffix=" UN",
            expander_label=f"Ver todos os {len(op_un)} operadores (UN)",
            delta_col="Total_KG",
            delta_fmt=",.0f",
            delta_suffix=" KG",
        )
        st.plotly_chart(
            bar_chart(op_un["Nome Curto"], op_un["Total_UN"], fmt=",.0f", cor="#5CB85C", max_show=10),
            use_container_width=True,
        )

    quebra_pagina()

    # ── Máquinas ──────────────────────────────────────────────────────────────
    st.subheader("🏭 Ranking de Máquinas")
    maq_totais = (
        df.groupby("Máquina")
        .agg(Total_KG=("Peso (KG)", "sum"), Total_UN=("Qtd (UN)", "sum"))
        .reset_index()
    )
    maq_totais["Máquina Curta"] = (
        maq_totais["Máquina"].str.extract(r"^(\d+)")[0].fillna("")
        + " - "
        + maq_totais["Máquina"].str.split(" - ").str[1:].str.join(" ").str[:18]
    )
    maq_totais["Nº Máquina"] = maq_totais["Máquina"].str.extract(r"^(\d+)")[0].fillna(maq_totais["Máquina"])

    col_mq1, col_mq2 = st.columns(2)

    with col_mq1:
        st.markdown("#### 📦 Total de KG por Máquina")
        maq_kg = maq_totais.sort_values("Total_KG", ascending=False).reset_index(drop=True)
        _cards_expander(
            maq_kg,
            label_col="Nº Máquina",
            value_col="Total_KG",
            value_fmt=",.0f",
            value_suffix=" KG",
            expander_label=f"Ver todas as {len(maq_kg)} máquinas (KG)",
            delta_col="Total_UN",
            delta_fmt=",.0f",
            delta_suffix=" UN",
        )
        st.plotly_chart(
            bar_chart(maq_kg["Máquina Curta"], maq_kg["Total_KG"], fmt=",.0f", cor="#4C9BE8", max_show=5),
            use_container_width=True,
        )

    with col_mq2:
        st.markdown("#### 🔢 Total de UN por Máquina")
        maq_un = maq_totais.sort_values("Total_UN", ascending=False).reset_index(drop=True)
        _cards_expander(
            maq_un,
            label_col="Nº Máquina",
            value_col="Total_UN",
            value_fmt=",.0f",
            value_suffix=" UN",
            expander_label=f"Ver todas as {len(maq_un)} máquinas (UN)",
            delta_col="Total_KG",
            delta_fmt=",.0f",
            delta_suffix=" KG",
        )
        st.plotly_chart(
            bar_chart(maq_un["Máquina Curta"], maq_un["Total_UN"], fmt=",.0f", cor="#5CB85C", max_show=5),
            use_container_width=True,
        )
# ── Aba 9: Comparativo por Máquina ───────────────────────────────────────────
with aba9:
    _MIN_DIAS = 5   # mínimo de dias na máquina para entrar na comparação

    st.header("🎯 Comparativo Justo por Máquina")
    st.caption(
        f"Compara operadores que trabalharam na mesma máquina por pelo menos **{_MIN_DIAS} dias** "
        "(≈ 1 semana). Base justa: mesmo equipamento, mesmo turno, mesmo mix de material."
    )

    # ── Seletores de máquina e turno ─────────────────────────────────────────
    _TODAS   = "🏭 Todas as máquinas"
    _TURNOS_DISP = ["Todos os turnos"] + sorted(df["Turno"].unique())
    _col_maq_sel, _col_turno_sel = st.columns([3, 1])
    _maq_cmp = _col_maq_sel.selectbox(
        "Selecione a máquina:",
        [_TODAS] + sorted(df["Máquina"].unique()),
        format_func=lambda m: m[:80],
        key="sel_maq_cmp",
    )
    _turno_cmp = _col_turno_sel.selectbox(
        "Turno:",
        _TURNOS_DISP,
        key="sel_turno_cmp",
    )

    # ── Visão consolidada — Todas as máquinas ────────────────────────────────
    # Aplica filtro de turno ao dataframe base desta aba
    _df_cmp_base = df if _turno_cmp == "Todos os turnos" else df[df["Turno"] == _turno_cmp]
    _turno_label = "" if _turno_cmp == "Todos os turnos" else f" — {_turno_cmp}"

    if _maq_cmp == _TODAS:
        st.subheader(f"📊 Impacto consolidado — Todas as máquinas{_turno_label}")
        st.caption(f"Considera apenas operadores com ≥ {_MIN_DIAS} dias em cada máquina.")

        _criticos_total = 0
        _amarelos_total = 0
        _gap_total = 0.0
        _potencial_total = 0.0
        _producao_real_total = 0.0
        _total_ops_analisados = 0
        _real_periodo_total = 0.0   # KG efetivamente produzido no período
        _proj_periodo_total = 0.0   # KG projetado se todos no nível do melhor
        _linhas_resumo = []
        _por_maquina = []   # resumo por máquina para cards individuais

        for _maq_iter in sorted(_df_cmp_base["Máquina"].unique()):
            _df_it = _df_cmp_base[_df_cmp_base["Máquina"] == _maq_iter]
            _dias_it = _df_it.groupby("Operador")["Data"].nunique()
            _ops_it = _dias_it[_dias_it >= _MIN_DIAS].index.tolist()
            if not _ops_it:
                continue
            _df_it_v = _df_it[_df_it["Operador"].isin(_ops_it)]

            # KG/hora por operador nessa máquina
            _horas_it = {}
            _dias_it_v = {}
            for _op, _g in _df_it_v.groupby("Operador"):
                _ts = _g["Turno"].iloc[0]; _fmt = _g["Formato"].iloc[0]
                _pi = _g["Periodo_Inicio_dt"].iloc[0]; _pf = _g["Periodo_Fim_dt"].iloc[0]
                if _fmt == "quinzenal" and pd.notna(_pi) and pd.notna(_pf):
                    _horas_it[_op] = horas_operador_periodo(_ts, _pi, _pf)
                    _dias_it_v[_op] = dias_trabalhados_no_periodo(_pi, _pf)
                else:
                    _du = _g["Data_dt"].dropna().drop_duplicates()
                    _horas_it[_op] = _du.apply(lambda d: horas_turno(_ts, d)).sum()
                    _dias_it_v[_op] = len(_du)

            _r_it = (
                _df_it_v.groupby(["Operador", "Nome Curto", "Turno"])
                .agg(Total_KG=("Peso (KG)", "sum"))
                .reset_index()
            )
            _r_it["Horas"]     = _r_it["Operador"].map(_horas_it)
            _r_it["Dias"]      = _r_it["Operador"].map(_dias_it_v)
            _r_it["KG/Hora"]   = (_r_it["Total_KG"] / _r_it["Horas"]).round(2)
            _r_it["KG/Dia"]    = (_r_it["Total_KG"] / _r_it["Dias"]).round(1)
            _r_it = _r_it.sort_values("KG/Hora", ascending=False)

            _melhor_h_it = _r_it["KG/Hora"].iloc[0]
            _melhor_d_it = _r_it["KG/Dia"].iloc[0]
            _melhor_nome = _r_it["Nome Curto"].iloc[0]

            _gap_maq = 0.0
            _pot_maq = 0.0
            _real_maq = 0.0
            _crit_maq = 0

            for _, _row in _r_it.iterrows():
                _pct = (_row["KG/Hora"] - _melhor_h_it) / _melhor_h_it * 100 if _melhor_h_it > 0 else 0
                _sem = "🟢" if _pct >= -10 else ("🟡" if _pct >= -25 else "🔴")
                if _sem == "🔴":
                    _criticos_total += 1
                    _crit_maq += 1
                elif _sem == "🟡":
                    _amarelos_total += 1

                # ── Gap dia a dia ─────────────────────────────────────────────
                # Para cada dia que o operador trabalhou nesta máquina:
                #   gap_dia = max(0, melhor_KG/h × horas_do_dia - KG_produzido_no_dia)
                # Só conta os dias em que ficou abaixo do melhor.
                # Dias em outras máquinas não são afetados.
                _op_key  = _row["Operador"]
                _ts_op   = _df_it_v[_df_it_v["Operador"] == _op_key]["Turno"].iloc[0]
                _fmt_op  = _df_it_v[_df_it_v["Operador"] == _op_key]["Formato"].iloc[0]
                _kg_real_op = _row["Total_KG"]

                if _fmt_op == "diario":
                    # Agrupa KG produzido por dia nesta máquina
                    _daily = (
                        _df_it_v[_df_it_v["Operador"] == _op_key]
                        .groupby("Data_dt")["Peso (KG)"].sum()
                    )
                    _gap_op = 0.0
                    for _dt, _kg_dia in _daily.items():
                        _h_dia = horas_turno(_ts_op, _dt)
                        _gap_op += max(0.0, _melhor_h_it * _h_dia - _kg_dia)
                else:
                    # Formato quinzenal: sem dado diário → usa período completo
                    _horas_op = _horas_it.get(_op_key, 0)
                    _gap_op   = max(0.0, _melhor_h_it * _horas_op - _kg_real_op)

                _kg_pot_op = _kg_real_op + _gap_op  # o que poderia ter produzido

                _gap_total           += _gap_op
                _potencial_total     += _kg_pot_op
                _producao_real_total += _kg_real_op
                _total_ops_analisados += 1
                _gap_maq  += _gap_op
                _pot_maq  += _kg_pot_op
                _real_maq += _kg_real_op
                _real_periodo_total  += _kg_real_op
                _proj_periodo_total  += _kg_pot_op

                _linhas_resumo.append({
                    "Máquina":        _maq_iter.split(" - ")[0],
                    "Operador":       _row["Nome Curto"],
                    "Turno":          _row["Turno"],
                    "Semáforo":       _sem,
                    "vs Melhor":      f"{_pct:+.1f}%",
                    "KG / Hora":      _row["KG/Hora"],
                    "KG / Dia":       _row["KG/Dia"],
                    "KG no período":  int(_kg_real_op),
                    "Gap no período": f"{int(-_gap_op):+,}",
                    "Melhor ref.":    _melhor_nome,
                })

            _por_maquina.append({
                "maq":       _maq_iter,
                "maq_curta": _maq_iter.split(" - ")[0],
                "n_ops":     len(_r_it),
                "criticos":  _crit_maq,
                "gap_mes":   _gap_maq,
                "pot_mes":   _pot_maq,
                "real_mes":  _real_maq,
                "melhor_nome": _melhor_nome,
                "melhor_kg_dia": _melhor_d_it,
                "r_it":      _r_it,   # dataframe de operadores desta máquina
            })

        # ── Cálculo unificado do ganho potencial ─────────────────────────────
        # Usa o total real do relatório + % de ganho dos operadores comparáveis
        # (mesma base para todos os cards — evita inconsistência)
        _total_relatorio = _df_cmp_base["Peso (KG)"].sum()
        _pct_gp          = ((_proj_periodo_total - _real_periodo_total) / _real_periodo_total * 100) if _real_periodo_total > 0 else 0
        _projetado_total = _total_relatorio * (1 + _pct_gp / 100)
        _ganho_real      = _projetado_total - _total_relatorio

        # Cards consolidados — uma linha, quatro cards
        st.caption("📅 Gap calculado dia a dia, nas horas reais de cada operador em cada máquina.")
        _pct_atencao = ((_criticos_total + _amarelos_total) / _total_ops_analisados * 100) if _total_ops_analisados > 0 else 0
        _pct_gap     = (_gap_total / _producao_real_total * 100) if _producao_real_total > 0 else 0

        _c1, _c2, _c3, _c4 = st.columns(4)
        _c1.metric(
            "⚠️ Operadores em atenção",
            f"🔴 {_criticos_total}  ·  🟡 {_amarelos_total}",
            delta=f"{_pct_atencao:.0f}% dos operadores analisados",
            delta_color="off",
        )
        _c2.metric(
            "📦 Total produzido no período",
            f"{_total_relatorio:,.0f} KG",
            delta="Total real do relatório",
            delta_color="off",
        )
        _c3.metric(
            "📉 KG deixados na mesa",
            f"{_gap_total:,.0f} KG",
            delta=f"+{_pct_gap:.1f}% — nos dias trabalhados, se igual ao melhor",
            delta_color="normal",
        )
        _c4.metric(
            "🚀 Projetado se todos no nível do melhor",
            f"{_projetado_total:,.0f} KG",
            delta=f"+{_ganho_real:,.0f} KG (+{_pct_gp:.1f}%)",
            delta_color="normal",
        )

        st.divider()
        st.subheader("⚠️ Operadores em atenção por máquina")
        st.caption("🔴 mais de 25% abaixo do melhor  ·  🟡 entre 10% e 25% abaixo do melhor")
        if _linhas_resumo:
            _df_resumo_all = pd.DataFrame(_linhas_resumo).sort_values(
                ["Máquina", "KG / Hora"], ascending=[True, False]
            )
            # Ordena do pior (vs Melhor mais negativo) para o menos pior
            _df_atencao = (
                _df_resumo_all[_df_resumo_all["Semáforo"].isin(["🔴", "🟡"])]
                .assign(_pct_num=lambda d: d["vs Melhor"].str.replace("%", "").str.replace("+", "").astype(float))
                .sort_values("_pct_num", ascending=True)
                .drop(columns="_pct_num")
                .reset_index(drop=True)
            )
            if _df_atencao.empty:
                st.success("✅ Nenhum operador em atenção no período — todos dentro de 10% do melhor em cada máquina.")
            else:
                st.dataframe(_df_atencao, use_container_width=True, hide_index=True)

    else:
        # ── Visão por máquina específica ─────────────────────────────────────
        _df_maq_c = _df_cmp_base[_df_cmp_base["Máquina"] == _maq_cmp].copy()

        # Dias por operador nessa máquina
        _dias_op_maq = _df_maq_c.groupby("Operador")["Data"].nunique()
        _ops_ok  = _dias_op_maq[_dias_op_maq >= _MIN_DIAS].index.tolist()
        _ops_exc = _dias_op_maq[_dias_op_maq <  _MIN_DIAS].sort_values(ascending=False)

        if _ops_exc.shape[0] > 0:
            _exc_str = " · ".join(
                f"{nome_curto(op)} ({d} dia{'s' if d>1 else ''})"
                for op, d in _ops_exc.items()
            )
            st.info(f"⚠️ Excluídos por < {_MIN_DIAS} dias na máquina: {_exc_str}")

        if not _ops_ok:
            st.warning(f"Nenhum operador com {_MIN_DIAS}+ dias nesta máquina no período carregado.")
        else:
            _df_validos = _df_maq_c[_df_maq_c["Operador"].isin(_ops_ok)].copy()

            # ── Cálculo de horas reais por operador (respeita turno e sábado) ────
            _horas_cmp = {}
            _dias_cmp  = {}
            for _op, _g in _df_validos.groupby("Operador"):
                _ts   = _g["Turno"].iloc[0]
                _fmt  = _g["Formato"].iloc[0]
                _pini = _g["Periodo_Inicio_dt"].iloc[0]
                _pfim = _g["Periodo_Fim_dt"].iloc[0]
                if _fmt == "quinzenal" and pd.notna(_pini) and pd.notna(_pfim):
                    _horas_cmp[_op] = horas_operador_periodo(_ts, _pini, _pfim)
                    _dias_cmp[_op]  = dias_trabalhados_no_periodo(_pini, _pfim)
                else:
                    _du = _g["Data_dt"].dropna().drop_duplicates()
                    _horas_cmp[_op] = _du.apply(lambda d: horas_turno(_ts, d)).sum()
                    _dias_cmp[_op]  = len(_du)

            # ── Resumo por operador ───────────────────────────────────────────────
            _r_cmp = (
                _df_validos.groupby(["Operador", "Nome Curto", "Turno"])
                .agg(
                    Total_KG=("Peso (KG)", "sum"),
                    Total_UN=("Qtd (UN)", "sum"),
                    Itens=("Cód Item", "nunique"),
                    Apontamentos=("Cód Item", "count"),
                )
                .reset_index()
            )
            _r_cmp["Dias na Máq."] = _r_cmp["Operador"].map(_dias_cmp)
            _r_cmp["Horas"]        = _r_cmp["Operador"].map(_horas_cmp).round(1)
            _r_cmp["KG / Hora"]    = (_r_cmp["Total_KG"] / _r_cmp["Horas"]).round(2)
            _r_cmp["KG / Dia"]     = (_r_cmp["Total_KG"] / _r_cmp["Dias na Máq."]).round(1)
            _r_cmp["KG / Mês*"]   = (_r_cmp["KG / Dia"] * 22).round(0).astype(int)
            _r_cmp = _r_cmp.sort_values("KG / Hora", ascending=False).reset_index(drop=True)

            # ── Semáforo vs. melhor ───────────────────────────────────────────────
            _melhor_h = _r_cmp["KG / Hora"].iloc[0]
            _melhor_d = _r_cmp["KG / Dia"].iloc[0]

            def _semaforo(val, ref):
                pct = (val - ref) / ref * 100 if ref > 0 else 0
                if pct >= -10:  return "🟢", pct
                if pct >= -25:  return "🟡", pct
                return "🔴", pct

            _r_cmp["Semáforo"]    = _r_cmp["KG / Hora"].apply(lambda v: _semaforo(v, _melhor_h)[0])
            _r_cmp["vs Melhor"]   = _r_cmp["KG / Hora"].apply(lambda v: f"{_semaforo(v, _melhor_h)[1]:+.1f}%")
            _r_cmp["Gap KG/Dia"]  = (_r_cmp["KG / Dia"] - _melhor_d).round(1).apply(lambda v: f"{v:+.1f}")
            _r_cmp["Gap KG/Mês"]  = ((_r_cmp["KG / Dia"] - _melhor_d) * 22).round(0).astype(int).apply(lambda v: f"{v:+,}")

            # ── Cards: melhor operador por turno (ou top-3 se turno filtrado) ───────
            _med_cmp = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            if _turno_cmp == "Todos os turnos":
                # _r_cmp já está ordenado por KG/Hora desc → drop_duplicates mantém o melhor de cada turno
                _cards_idx = _r_cmp.drop_duplicates(subset="Turno").index
                _cards_df  = _r_cmp.loc[_cards_idx].reset_index(drop=True)
                _rest_df   = _r_cmp.drop(index=_cards_idx).reset_index(drop=True)
            else:
                _cards_df = _r_cmp.head(3).reset_index(drop=True)
                _rest_df  = _r_cmp.iloc[3:].reset_index(drop=True)

            _cols_cmp = st.columns(max(len(_cards_df), 1))
            for _i, (_, _row) in enumerate(_cards_df.iterrows()):
                _sem, _pct = _semaforo(_row["KG / Hora"], _melhor_h)
                _medal = _med_cmp[_i] if _i < len(_med_cmp) else "·"
                with _cols_cmp[_i]:
                    st.metric(
                        label=f"{_medal} {_row['Nome Curto']} {_sem} · {_row['Turno']}",
                        value=f"{_row['KG / Hora']:.2f} KG/h",
                        delta=f"{_row['vs Melhor']} vs melhor · {_row['Dias na Máq.']} dias",
                    )
            if not _rest_df.empty:
                with st.expander(f"Ver demais {len(_rest_df)} operador(es)"):
                    _cols_r = st.columns(min(len(_rest_df), 4))
                    for _j, (_, _row) in enumerate(_rest_df.iterrows()):
                        _sem, _ = _semaforo(_row["KG / Hora"], _melhor_h)
                        with _cols_r[_j % 4]:
                            st.metric(
                                label=f"{_row['Nome Curto']} {_sem} · {_row['Turno']}",
                                value=f"{_row['KG / Hora']:.2f} KG/h",
                                delta=f"{_row['vs Melhor']} · {_row['Dias na Máq.']} dias",
                            )

            quebra_pagina()

            # ── Gráficos lado a lado ──────────────────────────────────────────────
            _cores_sem = [
                "#2ECC71" if _semaforo(v, _melhor_h)[0] == "🟢"
                else ("#F39C12" if _semaforo(v, _melhor_h)[0] == "🟡" else "#E74C3C")
                for v in _r_cmp["KG / Hora"]
            ]

            _col_g1, _col_g2, _col_g3 = st.columns(3)
            with _col_g1:
                st.markdown("**KG / Hora**")
                _fig1 = go.Figure(go.Bar(
                    x=_r_cmp["Nome Curto"], y=_r_cmp["KG / Hora"],
                    text=_r_cmp["KG / Hora"].apply(lambda v: f"{v:.2f}"),
                    textposition="inside", textfont=dict(size=13, color="white"),
                    marker_color=_cores_sem,
                ))
                _fig1.update_layout(
                    height=300, bargap=0.4, margin=dict(t=10, b=10, l=10, r=10),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                )
                st.plotly_chart(_fig1, use_container_width=True)

            with _col_g2:
                st.markdown("**KG / Dia**")
                _fig2 = go.Figure(go.Bar(
                    x=_r_cmp["Nome Curto"], y=_r_cmp["KG / Dia"],
                    text=_r_cmp["KG / Dia"].apply(lambda v: f"{v:.1f}"),
                    textposition="inside", textfont=dict(size=13, color="white"),
                    marker_color=_cores_sem,
                ))
                _fig2.update_layout(
                    height=300, bargap=0.4, margin=dict(t=10, b=10, l=10, r=10),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                )
                st.plotly_chart(_fig2, use_container_width=True)

            with _col_g3:
                st.markdown("**KG / Mês projetado** *(KG/dia × 22)*")
                _fig3 = go.Figure(go.Bar(
                    x=_r_cmp["Nome Curto"], y=_r_cmp["KG / Mês*"],
                    text=_r_cmp["KG / Mês*"].apply(lambda v: f"{v:,.0f}"),
                    textposition="inside", textfont=dict(size=13, color="white"),
                    marker_color=_cores_sem,
                ))
                _fig3.update_layout(
                    height=300, bargap=0.4, margin=dict(t=10, b=10, l=10, r=10),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"), yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                )
                st.plotly_chart(_fig3, use_container_width=True)

            quebra_pagina()

            # ── Tabela completa ───────────────────────────────────────────────────
            st.subheader("📋 Tabela detalhada")
            st.caption("🟢 até 10% abaixo do melhor  ·  🟡 entre 10% e 25%  ·  🔴 mais de 25% abaixo")
            _df_exib_cmp = _r_cmp[[
                "Semáforo", "Nome Curto", "Turno", "Dias na Máq.", "Horas",
                "KG / Hora", "KG / Dia", "KG / Mês*",
                "vs Melhor", "Gap KG/Dia", "Gap KG/Mês", "Itens", "Total_KG", "Total_UN",
            ]].rename(columns={
                "Nome Curto": "Operador",
                "Total_KG": "Total KG",
                "Total_UN": "Total UN",
                "Gap KG/Mês": "Gap KG/Mês*",
            }).copy()

            # ── Linha de total / média ────────────────────────────────────────────
            _tot = {
                "Semáforo":     "―",
                "Operador":     "📊 MÉDIA / TOTAL",
                "Turno":        "",
                "Dias na Máq.": f"{_r_cmp['Dias na Máq.'].mean():.1f} méd",
                "Horas":        round(_r_cmp["Horas"].mean(), 1),
                "KG / Hora":    round(_r_cmp["KG / Hora"].mean(), 2),        # média
                "KG / Dia":     round(_r_cmp["KG / Dia"].mean(), 1),         # média
                "KG / Mês*":    int(_r_cmp["KG / Mês*"].mean()),             # média
                "vs Melhor":    f"{((_r_cmp['KG / Hora'].mean()-_melhor_h)/_melhor_h*100):+.1f}%",
                "Gap KG/Dia":   f"{(_r_cmp['KG / Dia'].mean()-_melhor_d):+.1f}",
                "Gap KG/Mês*":  f"{int((_r_cmp['KG / Dia'].mean()-_melhor_d)*22):+,}",
                "Itens":        "―",
                "Total KG":     round(_r_cmp["Total_KG"].sum(), 1),          # soma
                "Total UN":     int(_r_cmp["Total_UN"].sum()),               # soma
            }
            _df_exib_final = pd.concat(
                [_df_exib_cmp, pd.DataFrame([_tot])], ignore_index=True
            )
            st.dataframe(_df_exib_final, use_container_width=True, hide_index=True)

            # ── Impacto do gap — dia a dia nos dias reais trabalhados ─────────────
            st.divider()
            st.subheader("💰 Impacto do gap de produtividade")
            st.caption(
                "Nos dias em que cada operador trabalhou nesta máquina: "
                "quanto a mais teria produzido se fosse igual ao melhor (KG/hora)?"
            )

            # Gap dia a dia: para cada operador, cada dia nesta máquina
            _gap_maq_periodo  = 0.0
            _real_maq_periodo = 0.0
            for _op_g, _g in _df_validos.groupby("Operador"):
                _ts_g  = _g["Turno"].iloc[0]
                _fmt_g = _g["Formato"].iloc[0]
                _real_g = _g["Peso (KG)"].sum()
                _real_maq_periodo += _real_g
                if _fmt_g == "diario":
                    for _dt, _kg_d in _g.groupby("Data_dt")["Peso (KG)"].sum().items():
                        _h_d = horas_turno(_ts_g, _dt)
                        _gap_maq_periodo += max(0.0, _melhor_h * _h_d - _kg_d)
                else:
                    _gap_maq_periodo += max(0.0, _melhor_h * _horas_cmp.get(_op_g, 0) - _real_g)

            _total_maq_relatorio = _df_cmp_base[_df_cmp_base["Máquina"] == _maq_cmp]["Peso (KG)"].sum()
            _pct_gap_maq_per = (_gap_maq_periodo / _real_maq_periodo * 100) if _real_maq_periodo > 0 else 0
            _projetado_maq   = _total_maq_relatorio * (1 + _pct_gap_maq_per / 100)
            _ganho_real_maq  = _projetado_maq - _total_maq_relatorio
            _n_criticos_maq  = len(_r_cmp[_r_cmp["Semáforo"] == "🔴"])
            _n_amarelos_maq  = len(_r_cmp[_r_cmp["Semáforo"] == "🟡"])
            _pct_atencao_maq = ((_n_criticos_maq + _n_amarelos_maq) / len(_r_cmp) * 100) if len(_r_cmp) > 0 else 0

            _c1, _c2, _c3, _c4 = st.columns(4)
            _c1.metric(
                "⚠️ Operadores em atenção",
                f"🔴 {_n_criticos_maq}  ·  🟡 {_n_amarelos_maq}",
                delta=f"{_pct_atencao_maq:.0f}% dos operadores",
                delta_color="off",
            )
            _c2.metric(
                "📦 Total produzido no período",
                f"{_total_maq_relatorio:,.0f} KG",
                delta="Total real da máquina",
                delta_color="off",
            )
            _c3.metric(
                "📉 KG deixados na mesa",
                f"{_gap_maq_periodo:,.0f} KG",
                delta=f"+{_pct_gap_maq_per:.1f}% — nos dias trabalhados, se igual ao melhor",
                delta_color="normal",
            )
            _c4.metric(
                "🚀 Projetado se todos no nível do melhor",
                f"{_projetado_maq:,.0f} KG",
                delta=f"+{_ganho_real_maq:,.0f} KG (+{_pct_gap_maq_per:.1f}%)",
                delta_color="normal",
            )


# ── Aba 6: KG / Dia ──────────────────────────────────────────────────────────
with aba6:
    st.header("📅 Ranking por KG / Dia")
    st.caption("Média de quilos produzidos por dia trabalhado — métrica que mostra ritmo diário independente do turno.")

    _med_dia = ["🥇", "🥈", "🥉"]

    def _cards_kgdia(df_rank, label_col, value_col, value_suffix, extra_col, extra_suffix, expander_txt, n_cols=5):
        n = len(df_rank)
        top3 = min(n, 3)
        cols = st.columns(top3)
        for i in range(top3):
            row = df_rank.iloc[i]
            with cols[i]:
                st.metric(
                    label=f"{_med_dia[i]} {row[label_col]}",
                    value=f"{row[value_col]:,.1f} {value_suffix}",
                    delta=f"{row[extra_col]:,.1f} {extra_suffix}" if extra_col else None,
                )
        if n > 3:
            with st.expander(expander_txt):
                rest = df_rank.iloc[3:]
                cols2 = st.columns(min(len(rest), n_cols))
                for j, (_, row) in enumerate(rest.iterrows()):
                    with cols2[j % n_cols]:
                        st.metric(
                            label=row[label_col],
                            value=f"{row[value_col]:,.1f} {value_suffix}",
                            delta=f"{row[extra_col]:,.1f} {extra_suffix}" if extra_col else None,
                        )

    # ── Operadores ────────────────────────────────────────────────────────────
    st.subheader("👷 Operadores — KG / Dia")

    op_dia = resumo.sort_values("KG / Dia", ascending=False).reset_index(drop=True)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.markdown("#### 📦 KG / Dia")
        _cards_kgdia(
            op_dia, "Nome Curto", "KG / Dia", "KG/dia",
            "KG / Hora", "KG/hora",
            f"Ver todos os {len(op_dia)} operadores",
        )
        st.plotly_chart(
            bar_chart(op_dia["Nome Curto"], op_dia["KG / Dia"], fmt=",.1f", cor="#4C9BE8", max_show=10),
            use_container_width=True,
        )

    with col_d2:
        st.markdown("#### 🕐 Horas trabalhadas no período")
        op_horas = resumo.sort_values("Horas Trabalhadas", ascending=False).reset_index(drop=True)
        _cards_kgdia(
            op_horas, "Nome Curto", "Horas Trabalhadas", "h",
            "Dias Trabalhados", "dias",
            f"Ver todos os {len(op_horas)} operadores",
        )
        st.plotly_chart(
            bar_chart(op_horas["Nome Curto"], op_horas["Horas Trabalhadas"], fmt=",.0f", cor="#F0AD4E", max_show=10),
            use_container_width=True,
        )

    st.divider()

    # ── Tabela resumo operadores ──────────────────────────────────────────────
    st.subheader("📋 Tabela completa — Operadores")
    st.dataframe(
        op_dia[[
            "Nome Curto", "Turno", "Dias Trabalhados", "Horas Trabalhadas",
            "KG / Dia", "KG / Hora", "UN / Hora", "Total_KG", "Total_UN",
        ]].rename(columns={
            "Nome Curto": "Operador",
            "Total_KG": "Total KG",
            "Total_UN": "Total UN",
        }),
        use_container_width=True, hide_index=True,
    )

    quebra_pagina()

    # ── Máquinas ──────────────────────────────────────────────────────────────
    st.subheader("🏭 Máquinas — KG / Dia")

    maq_dia = df_maq_resumo_g.sort_values("KG / Dia", ascending=False).reset_index(drop=True)

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        st.markdown("#### 📦 KG / Dia por Máquina")
        _cards_kgdia(
            maq_dia, "Nº Máquina", "KG / Dia", "KG/dia",
            "UN / Dia", "UN/dia",
            f"Ver todas as {len(maq_dia)} máquinas",
            n_cols=4,
        )
        st.plotly_chart(
            bar_chart(maq_dia["Máquina Curta"], maq_dia["KG / Dia"], fmt=",.1f", cor="#4C9BE8", max_show=5),
            use_container_width=True,
        )

    with col_m2:
        st.markdown("#### 🔢 UN / Dia por Máquina")
        maq_un_dia = df_maq_resumo_g.sort_values("UN / Dia", ascending=False).reset_index(drop=True)
        _cards_kgdia(
            maq_un_dia, "Nº Máquina", "UN / Dia", "UN/dia",
            "KG / Dia", "KG/dia",
            f"Ver todas as {len(maq_un_dia)} máquinas",
            n_cols=4,
        )
        st.plotly_chart(
            bar_chart(maq_un_dia["Máquina Curta"], maq_un_dia["UN / Dia"], fmt=",.0f", cor="#5CB85C", max_show=5),
            use_container_width=True,
        )

    st.divider()

    st.subheader("📋 Tabela completa — Máquinas")
    st.dataframe(
        maq_dia[[
            "Nº Máquina", "Máquina", "Dias_Ativas", "KG / Dia", "UN / Dia",
            "Total_KG", "Total_UN", "Melhor Op", "Melhor KG/Dia",
        ]].rename(columns={
            "Nº Máquina": "Máq.",
            "Dias_Ativas": "Dias Ativas",
            "Total_KG": "Total KG",
            "Total_UN": "Total UN",
            "Melhor Op": "Melhor Operador",
        }),
        use_container_width=True, hide_index=True,
    )


# ── Aba 7: Resumo Geral ──────────────────────────────────────────────────────
with aba7:
    st.header("📊 Resumo Geral de Produção")
    st.caption("Soma total produzida no período analisado, por operador e por máquina.")

    # ── Tabela de Operadores ──────────────────────────────────────────────────
    st.subheader("👷 Produção por Operador")

    _op_un_rg = df[df["Unidade"] == "UN"].groupby("Operador")["Qtd (UN)"].sum().rename("Total_UN")
    op_resumo = (
        df.groupby(["Operador", "Nome Curto", "Turno"])
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Apontamentos=("Cód Item", "count"),
        )
        .reset_index()
        .join(_op_un_rg, on="Operador")
        .sort_values("Total_KG", ascending=False)
        .reset_index(drop=True)
    )
    op_resumo["Total_UN"] = op_resumo["Total_UN"].fillna(0).astype(int)
    op_resumo.index = op_resumo.index + 1  # começa em 1

    # Linha de total
    total_op = pd.DataFrame([{
        "Operador": "─── TOTAL ───",
        "Nome Curto": "TOTAL",
        "Turno": "",
        "Total_KG": op_resumo["Total_KG"].sum(),
        "Total_UN": int(op_resumo["Total_UN"].sum()),
        "Apontamentos": op_resumo["Apontamentos"].sum(),
    }])
    total_op.index = [""]

    df_op_exib = pd.concat([
        op_resumo[["Operador", "Turno", "Total_KG", "Total_UN", "Apontamentos"]],
        total_op[["Operador", "Turno", "Total_KG", "Total_UN", "Apontamentos"]],
    ])

    st.dataframe(
        df_op_exib.rename(columns={
            "Total_KG": "Total KG",
            "Total_UN": "Total UN (peças)",
        }),
        use_container_width=True,
    )

    quebra_pagina()

    # ── Tabela de Máquinas ────────────────────────────────────────────────────
    st.subheader("🏭 Produção por Máquina")

    maq_resumo = (
        df.groupby("Máquina")
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Apontamentos=("Cód Item", "count"),
            Operadores=("Nome Curto", lambda x: len(x.unique())),
        )
        .reset_index()
        .join(df[df["Unidade"] == "UN"].groupby("Máquina")["Qtd (UN)"].sum().rename("Total_UN"), on="Máquina")
        .sort_values("Total_KG", ascending=False)
        .reset_index(drop=True)
    )
    maq_resumo["Total_UN"] = maq_resumo["Total_UN"].fillna(0).astype(int)
    maq_resumo.index = maq_resumo.index + 1

    total_maq = pd.DataFrame([{
        "Máquina": "─── TOTAL ───",
        "Total_KG": maq_resumo["Total_KG"].sum(),
        "Total_UN": int(maq_resumo["Total_UN"].sum()),
        "Apontamentos": maq_resumo["Apontamentos"].sum(),
        "Operadores": df["Nome Curto"].nunique(),
    }])
    total_maq.index = [""]

    df_maq_exib = pd.concat([
        maq_resumo[["Máquina", "Total_KG", "Total_UN", "Apontamentos", "Operadores"]],
        total_maq[["Máquina", "Total_KG", "Total_UN", "Apontamentos", "Operadores"]],
    ])

    st.dataframe(
        df_maq_exib.rename(columns={
            "Total_KG": "Total KG",
            "Total_UN": "Total UN (peças)",
            "Operadores": "Nº Operadores",
        }),
        use_container_width=True,
    )

    # ── Totalizador geral ─────────────────────────────────────────────────────
    quebra_pagina()
    c1, c2, c3, c4 = st.columns(4)
    _total_un_cards = int(df[df["Unidade"] == "UN"]["Qtd (UN)"].sum())
    _total_mt_cards = df[df["Unidade"] == "MT"]["Qtd (UN)"].sum()
    c1.metric("⚖️ Total KG", f"{df['Peso (KG)'].sum():,.3f} KG")
    c2.metric("🔢 Total peças (UN)", f"{_total_un_cards:,}",
              delta=f"+ {_total_mt_cards:,.1f} m em MT" if _total_mt_cards > 0 else None,
              delta_color="off")
    c3.metric("👷 Operadores", df["Operador"].nunique())
    c4.metric("🏭 Máquinas", df["Máquina"].nunique())

    # ── Validação contra o relatório original ─────────────────────────────────
    st.divider()
    st.subheader("🔍 Conferência com o relatório PDF")
    st.caption("Digite o **Total Geral de KG** do seu relatório PDF para conferir se todos os registros foram lidos corretamente.")
    _inp_pdf = st.text_input(
        "Total KG do relatório (formato do PDF, ex: 191.638,346 ou 191638.346)",
        value="", key="total_pdf_input",
        placeholder="191.638,346",
    )
    # Aceita tanto formato BR (191.638,346) quanto americano (191638.346)
    _total_pdf = 0.0
    if _inp_pdf.strip():
        try:
            _s = _inp_pdf.strip()
            if "," in _s and "." in _s:
                _total_pdf = float(_s.replace(".", "").replace(",", "."))
            elif "," in _s:
                _total_pdf = float(_s.replace(",", "."))
            else:
                _total_pdf = float(_s.replace(".", ""))
                if _total_pdf < 1000:   # provavelmente decimal americano
                    _total_pdf = float(_s)
        except ValueError:
            st.warning("⚠️ Formato inválido. Use: 191.638,346 ou 191638.346")
    if _total_pdf > 0:
        _total_lido = df["Peso (KG)"].sum()
        _diff = _total_lido - _total_pdf
        _pct_diff = (_diff / _total_pdf * 100)
        _cv1, _cv2, _cv3 = st.columns(3)
        _cv1.metric("📄 Total PDF", f"{_total_pdf:,.3f} KG")
        _cv2.metric("💻 Total lido pelo sistema", f"{_total_lido:,.3f} KG")
        _cv3.metric(
            "⚠️ Diferença",
            f"{_diff:+,.3f} KG",
            delta=f"{_pct_diff:+.2f}%",
            delta_color="inverse",
        )
        if abs(_pct_diff) < 0.1:
            st.success("✅ Totais conferem — todos os registros foram lidos corretamente.")
        elif abs(_pct_diff) < 2:
            st.warning(f"⚠️ Diferença pequena de {abs(_diff):,.1f} KG ({abs(_pct_diff):.2f}%) — pode ser arredondamento ou itens de recurso não-humano.")
        else:
            st.error(f"❌ Diferença de {abs(_diff):,.1f} KG ({abs(_pct_diff):.1f}%) — alguns registros podem não ter sido lidos. Verifique se o PDF está no formato RPCP621.")


# ── Aba 8: Dados Brutos ───────────────────────────────────────────────────────
with aba8:
    st.header("Base de Dados Unificada")

    col1, col2, col3 = st.columns(3)
    with col1:
        ops = ["Todos"] + sorted(df["Operador"].unique().tolist())
        op_sel = st.selectbox("Filtrar por Operador:", ops)
    with col2:
        turnos = ["Todos"] + sorted(df["Turno"].unique().tolist())
        turno_sel = st.selectbox("Filtrar por Turno:", turnos)
    with col3:
        maquinas = ["Todas"] + sorted(df["Máquina"].unique().tolist())
        maq_sel = st.selectbox("Filtrar por Máquina:", maquinas)

    df_exib = df.drop(columns=["Data_dt"])
    if op_sel != "Todos":
        df_exib = df_exib[df_exib["Operador"] == op_sel]
    if turno_sel != "Todos":
        df_exib = df_exib[df_exib["Turno"] == turno_sel]
    if maq_sel != "Todas":
        df_exib = df_exib[df_exib["Máquina"] == maq_sel]

    st.dataframe(df_exib, use_container_width=True, hide_index=True)
    csv = df_exib.to_csv(index=False).encode("utf-8")
    st.download_button(label="📥 Baixar CSV", data=csv, file_name="producao_polimpress.csv", mime="text/csv")


# ── Aba 5: Evolução Operador ──────────────────────────────────────────────────
with aba5:
    st.header("📈 Evolução Individual do Operador")
    st.caption("Carregue o relatório RPCP621 de um único operador com vários meses para ver a evolução mês a mês.")

    # Seleção do operador
    _ops_evo = sorted(df["Operador"].unique())
    if len(_ops_evo) == 1:
        _op_evo = _ops_evo[0]
        st.info(f"Operador detectado automaticamente: **{nome_curto(_op_evo)}** — {_op_evo}")
    else:
        _op_evo = st.selectbox(
            "Selecione o operador para análise de evolução:",
            _ops_evo,
            format_func=lambda x: f"{nome_curto(x)} — {x}",
            key="sel_evo",
        )

    _df_op = df[df["Operador"] == _op_evo].copy()
    _turno_op = _df_op["Turno"].iloc[0]
    _nc_op = _df_op["Nome Curto"].iloc[0]

    # Colunas de mês
    _df_op["_Mes_ord"] = _df_op["Data_dt"].dt.to_period("M")
    _df_op["_Mes_lbl"] = _df_op["Data_dt"].dt.strftime("%b/%Y")
    _meses_ord = sorted(_df_op["_Mes_ord"].unique())
    _mes_lbl_map = (
        _df_op.drop_duplicates("_Mes_ord")
        .set_index("_Mes_ord")["_Mes_lbl"]
        .to_dict()
    )

    if len(_meses_ord) < 2:
        st.warning("É necessário ao menos 2 meses de dados para análise de evolução.")

    # ── Resumo mensal ─────────────────────────────────────────────────────────
    _monthly = []
    for _mp in _meses_ord:
        _dfm = _df_op[_df_op["_Mes_ord"] == _mp]
        _dias_u = _dfm["Data_dt"].dropna().drop_duplicates()
        _horas_m = _dias_u.apply(lambda d: horas_turno(_turno_op, d)).sum()
        _dias_m = len(_dias_u)
        _kg = _dfm["Peso (KG)"].sum()
        _un = _dfm["Qtd (UN)"].sum()
        _monthly.append({
            "Mês":                _mes_lbl_map[_mp],
            "_ord":               _mp,
            "Dias":               _dias_m,
            "Horas":              round(_horas_m, 1),
            "Total KG":           round(_kg, 1),
            "Total UN":           int(_un),
            "KG / Hora":          round(_kg / _horas_m, 2) if _horas_m > 0 else 0,
            "KG / Dia":           round(_kg / _dias_m, 1) if _dias_m > 0 else 0,
            "UN / Hora":          round(_un / _horas_m, 1) if _horas_m > 0 else 0,
            "Itens Distintos":    _dfm["Cód Item"].nunique(),
            "Máquinas Usadas":    _dfm["Máquina"].nunique(),
            "Peso Médio/peça (g)": round(_dfm["Peso Médio/UN (g)"].mean(), 1),
        })
    _df_monthly = pd.DataFrame(_monthly)

    # Helper de tendência reutilizável
    def _tendencia(ini, fim, label, unidade):
        pct = (fim - ini) / ini * 100 if ini > 0 else 0
        mes_ini = _df_monthly.iloc[0]["Mês"]
        mes_fim = _df_monthly.iloc[-1]["Mês"]
        if pct > 3:
            txt = f"📈 Melhorando  (+{pct:.1f}% de {mes_ini} a {mes_fim})"
            cor = "green"
        elif pct < -3:
            txt = f"📉 Piorando  ({pct:.1f}% de {mes_ini} a {mes_fim})"
            cor = "red"
        else:
            txt = f"➡️ Estável  ({pct:+.1f}% de {mes_ini} a {mes_fim})"
            cor = "gray"
        return f"**Tendência {label}:** :{cor}[{txt}]"

    _t1 = _tendencia(
        _df_monthly.iloc[0]["KG / Hora"], _df_monthly.iloc[-1]["KG / Hora"],
        "KG/hora", "KG/h"
    )
    _t2 = _tendencia(
        _df_monthly.iloc[0]["KG / Dia"], _df_monthly.iloc[-1]["KG / Dia"],
        "KG/dia", "KG/dia"
    )
    _t3 = _tendencia(
        _df_monthly.iloc[0]["Total KG"], _df_monthly.iloc[-1]["Total KG"],
        "KG/mês", "KG"
    )
    st.markdown(_t1 + "   |   " + _t2 + "   |   " + _t3)

    # Cards por mês com delta vs mês anterior
    _med_evo = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
                "1️⃣1️⃣", "1️⃣2️⃣"]
    _n_mes = len(_df_monthly)
    _cols_mes = st.columns(_n_mes)
    for _i, (_, _row) in enumerate(_df_monthly.iterrows()):
        with _cols_mes[_i]:
            _dlt_h = _dlt_d = _dlt_m = None
            if _i > 0:
                _ph = _df_monthly.iloc[_i - 1]["KG / Hora"]
                _pd = _df_monthly.iloc[_i - 1]["KG / Dia"]
                _pm = _df_monthly.iloc[_i - 1]["Total KG"]
                _dlt_h = f"{'▲' if _row['KG / Hora'] >= _ph else '▼'} {abs(_row['KG / Hora'] - _ph):.2f} KG/h"
                _dlt_d = f"{'▲' if _row['KG / Dia']  >= _pd else '▼'} {abs(_row['KG / Dia']  - _pd):.1f} KG/dia"
                _dlt_m = f"{'▲' if _row['Total KG']  >= _pm else '▼'} {abs(_row['Total KG']  - _pm):,.0f} KG/mês"
            st.metric(
                label=f"{_med_evo[_i] if _i < len(_med_evo) else ''} {_row['Mês']}",
                value=f"{_row['KG / Hora']:.2f} KG/h",
                delta=_dlt_h,
            )
            st.metric(label="KG/dia", value=f"{_row['KG / Dia']:.1f}", delta=_dlt_d)
            st.metric(label="KG/mês", value=f"{_row['Total KG']:,.0f}", delta=_dlt_m)

    quebra_pagina()

    # ── Gráficos mensais ──────────────────────────────────────────────────────
    _col_e1, _col_e2 = st.columns(2)
    with _col_e1:
        st.subheader("KG / Hora por mês")
        st.plotly_chart(
            bar_chart(_df_monthly["Mês"], _df_monthly["KG / Hora"], fmt=".2f", cor="#4C9BE8"),
            use_container_width=True,
        )
    with _col_e2:
        st.subheader("KG / Dia por mês")
        st.plotly_chart(
            bar_chart(_df_monthly["Mês"], _df_monthly["KG / Dia"], fmt=".1f", cor="#5CB85C"),
            use_container_width=True,
        )

    _col_e3, _col_e4 = st.columns(2)
    with _col_e3:
        st.subheader("Total KG produzido por mês")
        st.plotly_chart(
            bar_chart(_df_monthly["Mês"], _df_monthly["Total KG"], fmt=",.0f", cor="#F0AD4E"),
            use_container_width=True,
        )
    with _col_e4:
        st.subheader("Peso médio/peça (g) por mês")
        st.caption("Peças mais pesadas = produtos maiores/mais complexos — máquina roda mais devagar.")
        st.plotly_chart(
            bar_chart(_df_monthly["Mês"], _df_monthly["Peso Médio/peça (g)"], fmt=".1f", cor="#D9534F"),
            use_container_width=True,
        )

    quebra_pagina()

    # ── Evolução diária (linha) ───────────────────────────────────────────────
    st.subheader("📅 KG/hora dia a dia — curva de evolução")
    st.caption("Cada ponto é um dia trabalhado. Ideal: pontos subindo ao longo do tempo.")
    _df_daily = (
        _df_op.groupby("Data_dt")
        .agg(KG=("Peso (KG)", "sum"), UN=("Qtd (UN)", "sum"))
        .reset_index()
    )
    _df_daily["Horas"] = _df_daily["Data_dt"].apply(lambda d: horas_turno(_turno_op, d))
    _df_daily["KG/hora"] = (_df_daily["KG"] / _df_daily["Horas"]).round(2)
    _df_daily["KG/dia"] = _df_daily["KG"].round(1)
    _df_daily["Mês"] = _df_daily["Data_dt"].dt.strftime("%b/%Y")

    _cor_meses = ["#4C9BE8", "#5CB85C", "#F0AD4E", "#D9534F",
                  "#9B59B6", "#1ABC9C", "#E74C3C", "#F39C12",
                  "#2ECC71", "#3498DB", "#E91E63", "#FF5722"]
    _fig_line = go.Figure()
    _media_geral = _df_daily["KG/hora"].mean()
    _fig_line.add_hline(
        y=_media_geral, line_dash="dot", line_color="gray",
        annotation_text=f"Média {_media_geral:.2f}", annotation_position="bottom right",
    )
    for _ci, _mp in enumerate(_meses_ord):
        _lbl = _mes_lbl_map[_mp]
        _sub = _df_daily[_df_daily["Mês"] == _lbl]
        _fig_line.add_trace(go.Scatter(
            x=_sub["Data_dt"], y=_sub["KG/hora"],
            mode="markers+lines", name=_lbl,
            line=dict(color=_cor_meses[_ci % len(_cor_meses)]),
            marker=dict(size=7),
            hovertemplate="%{x|%d/%m}<br>%{y:.2f} KG/h<extra></extra>",
        ))
    _fig_line.update_layout(
        height=380, margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.1)", title="KG/hora"),
        xaxis=dict(title=""),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(_fig_line, use_container_width=True)

    quebra_pagina()

    # ── Por máquina ───────────────────────────────────────────────────────────
    st.subheader("🏭 Máquinas por mês — presença e desempenho")

    # Base: máquina × mês — com KG, dias e KG/dia
    _df_maq_mes = (
        _df_op.groupby(["Máquina", "_Mes_ord", "_Mes_lbl"])
        .agg(KG=("Peso (KG)", "sum"), Dias=("Data", "nunique"), UN=("Qtd (UN)", "sum"))
        .reset_index()
    )
    _df_maq_mes["KG/dia"] = (_df_maq_mes["KG"] / _df_maq_mes["Dias"]).round(1)
    _df_maq_mes["Maq_curta"] = (
        _df_maq_mes["Máquina"].str.extract(r"^(\d+)")[0].fillna("")
        + " · "
        + _df_maq_mes["Máquina"].str.split(" - ").str[1:].str.join(" ").str[:18]
    )

    # ── Tabela de presença: máquina × mês ────────────────────────────────────
    st.markdown("#### 📅 Quantos dias trabalhou em cada máquina por mês")
    _pivot_dias = (
        _df_maq_mes.pivot_table(index="Maq_curta", columns="_Mes_lbl", values="Dias", aggfunc="sum", fill_value=0)
    )
    # Ordena colunas por mês cronológico
    _col_order = [_mes_lbl_map[m] for m in _meses_ord if _mes_lbl_map[m] in _pivot_dias.columns]
    _pivot_dias = _pivot_dias[_col_order]
    # Adiciona total
    _pivot_dias["Total dias"] = _pivot_dias.sum(axis=1)
    _pivot_dias = _pivot_dias.sort_values("Total dias", ascending=False)
    st.dataframe(_pivot_dias, use_container_width=True)

    # ── Tabela KG/dia: máquina × mês ─────────────────────────────────────────
    st.markdown("#### 📦 KG/dia em cada máquina por mês")
    _pivot_kgdia = (
        _df_maq_mes.pivot_table(index="Maq_curta", columns="_Mes_lbl", values="KG/dia", aggfunc="mean")
        .round(1)
    )
    _pivot_kgdia = _pivot_kgdia[[c for c in _col_order if c in _pivot_kgdia.columns]]
    # Tendência por linha: seta e %
    def _row_tend(row):
        vals = row.dropna()
        if len(vals) < 2:
            return "—"
        ini, fim = vals.iloc[0], vals.iloc[-1]
        pct = (fim - ini) / ini * 100 if ini > 0 else 0
        seta = "📈" if pct > 3 else ("📉" if pct < -3 else "➡️")
        return f"{seta} {pct:+.1f}%"
    _pivot_kgdia["Tendência"] = _pivot_kgdia.apply(_row_tend, axis=1)
    st.dataframe(_pivot_kgdia, use_container_width=True)

    quebra_pagina()

    # ── Gráfico de linha: KG/dia por máquina ao longo dos meses ──────────────
    st.markdown("#### 📈 Evolução do KG/dia por máquina mês a mês")
    _todas_maqs = sorted(_df_maq_mes["Máquina"].unique())
    _maq_multi = _df_maq_mes.groupby("Máquina")["_Mes_lbl"].nunique()
    _maq_multi_lst = _maq_multi[_maq_multi >= 2].index.tolist()

    _col_graf, _col_single = st.columns([3, 1])
    with _col_graf:
        if _maq_multi_lst:
            st.caption("Máquinas com presença em ≥ 2 meses — clique na legenda para ocultar/exibir")
            _fig_maq = go.Figure()
            for _ci, _maq in enumerate(_maq_multi_lst):
                _sub = _df_maq_mes[_df_maq_mes["Máquina"] == _maq].sort_values("_Mes_ord")
                _maq_s = _sub["Maq_curta"].iloc[0]
                _fig_maq.add_trace(go.Scatter(
                    x=_sub["_Mes_lbl"], y=_sub["KG/dia"],
                    mode="markers+lines+text", name=_maq_s,
                    text=_sub["KG/dia"].apply(lambda v: f"{v:.0f}"),
                    textposition="top center",
                    line=dict(color=_cor_meses[_ci % len(_cor_meses)], width=2),
                    marker=dict(size=10),
                    hovertemplate="%{x}<br>%{y:.1f} KG/dia<extra>" + _maq_s + "</extra>",
                ))
            _fig_maq.update_layout(
                height=360, margin=dict(t=30, b=10, l=10, r=10),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="white"),
                yaxis=dict(gridcolor="rgba(255,255,255,0.1)", title="KG/dia"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(_fig_maq, use_container_width=True)

    with _col_single:
        st.caption("Máquinas esporádicas (1 mês)")
        _maq_1mes = _maq_multi[_maq_multi == 1].index.tolist()
        if _maq_1mes:
            _df_esp = _df_maq_mes[_df_maq_mes["Máquina"].isin(_maq_1mes)][
                ["Maq_curta", "_Mes_lbl", "Dias", "KG/dia"]
            ].sort_values("KG/dia", ascending=False)
            st.dataframe(_df_esp.rename(columns={
                "Maq_curta": "Máquina", "_Mes_lbl": "Mês",
                "Dias": "Dias", "KG/dia": "KG/dia",
            }), use_container_width=True, hide_index=True)
        else:
            st.info("Todas as máquinas têm ≥ 2 meses.")

    # ── Cards de tendência por máquina principal ──────────────────────────────
    st.markdown("#### 🏅 Tendência individual por máquina")
    if _maq_multi_lst:
        _cols_maq_tend = st.columns(min(len(_maq_multi_lst), 4))
        for _ci, _maq in enumerate(_maq_multi_lst):
            _sub = _df_maq_mes[_df_maq_mes["Máquina"] == _maq].sort_values("_Mes_ord")
            _maq_s = _sub["Maq_curta"].iloc[0]
            _ini_v = _sub["KG/dia"].iloc[0]
            _fim_v = _sub["KG/dia"].iloc[-1]
            _pct_v = (_fim_v - _ini_v) / _ini_v * 100 if _ini_v > 0 else 0
            _dlt_str = f"{'📈' if _pct_v > 3 else ('📉' if _pct_v < -3 else '➡️')} {_pct_v:+.1f}%"
            with _cols_maq_tend[_ci % 4]:
                st.metric(
                    label=_maq_s,
                    value=f"{_fim_v:.1f} KG/dia",
                    delta=f"{_dlt_str}  ({_sub['_Mes_lbl'].iloc[0]} → {_sub['_Mes_lbl'].iloc[-1]})",
                )

    quebra_pagina()

    # ── Top itens ─────────────────────────────────────────────────────────────
    st.subheader("📦 Desempenho por item — quais produtos ele domina")
    _df_item_evo = (
        _df_op.groupby(["Cód Item", "Descrição Item"])
        .agg(KG=("Peso (KG)", "sum"), UN=("Qtd (UN)", "sum"), Dias=("Data", "nunique"), Apts=("Data", "count"))
        .reset_index()
    )
    _df_item_evo["KG/dia"] = (_df_item_evo["KG"] / _df_item_evo["Dias"]).round(1)
    _df_item_evo["Desc_curta"] = _df_item_evo["Descrição Item"].str[:30]
    _df_item_evo = _df_item_evo.sort_values("KG/dia", ascending=False).reset_index(drop=True)

    _col_it1, _col_it2 = st.columns([3, 2])
    with _col_it1:
        st.markdown("**Top itens por KG/dia** (mostrando top 15)")
        _top15 = _df_item_evo.head(15)
        st.plotly_chart(
            bar_chart(_top15["Desc_curta"], _top15["KG/dia"], fmt=".1f", cor="#F0AD4E", max_show=10),
            use_container_width=True,
        )
    with _col_it2:
        st.markdown("**Itens distintos produzidos por mês**")
        st.caption("Mais itens = mais troca de setup = possível queda de eficiência.")
        st.plotly_chart(
            bar_chart(
                _df_monthly["Mês"], _df_monthly["Itens Distintos"],
                fmt=".0f", cor="#D9534F",
            ),
            use_container_width=True,
        )

    quebra_pagina()

    # ── Consistência: box por mês ─────────────────────────────────────────────
    st.subheader("📊 Consistência diária por mês")
    st.caption("Quanto menor o espalhamento, mais consistente o operador. Pontos fora = dias ruins ou excepcionais.")
    _fig_box = go.Figure()
    for _ci, _mp in enumerate(_meses_ord):
        _lbl = _mes_lbl_map[_mp]
        _sub = _df_daily[_df_daily["Mês"] == _lbl]["KG/hora"]
        _fig_box.add_trace(go.Box(
            y=_sub, name=_lbl,
            marker_color=_cor_meses[_ci % len(_cor_meses)],
            boxpoints="all", jitter=0.3, pointpos=-1.5,
        ))
    _fig_box.update_layout(
        height=360, margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.1)", title="KG/hora"),
        showlegend=False,
    )
    st.plotly_chart(_fig_box, use_container_width=True)

    # ── Resumo automático de consistência ─────────────────────────────────────
    st.markdown("#### 🧠 Análise automática da consistência")

    # Agrega máquinas usadas por dia (para exibir junto com dias ruins)
    _df_maq_por_dia = (
        _df_op.groupby("Data_dt")["Máquina"]
        .apply(lambda x: ", ".join(sorted(x.str.extract(r"^(\d+)")[0].dropna().unique())))
        .reset_index()
        .rename(columns={"Máquina": "Máqs"})
    )
    _df_daily_maq = _df_daily.merge(_df_maq_por_dia, on="Data_dt", how="left")

    _stats_meses = []
    for _ci, _mp in enumerate(_meses_ord):
        _lbl = _mes_lbl_map[_mp]
        _sub_mes = _df_daily_maq[_df_daily_maq["Mês"] == _lbl]
        _vals = _sub_mes["KG/hora"]
        if len(_vals) == 0:
            continue
        _limiar_ruim  = _vals.mean() * 0.75
        _limiar_otimo = _vals.mean() * 1.25
        _df_ruins  = _sub_mes[_sub_mes["KG/hora"] < _limiar_ruim].sort_values("Data_dt")
        _df_otimos = _sub_mes[_sub_mes["KG/hora"] > _limiar_otimo].sort_values("Data_dt")
        _stats_meses.append({
            "lbl":         _lbl,
            "media":       _vals.mean(),
            "mediana":     _vals.median(),
            "std":         _vals.std(),
            "cv":          _vals.std() / _vals.mean() * 100 if _vals.mean() > 0 else 0,
            "min":         _vals.min(),
            "max":         _vals.max(),
            "dias":        len(_vals),
            "dias_ruins":  len(_df_ruins),
            "dias_otimos": len(_df_otimos),
            "df_ruins":    _df_ruins,   # DataFrame com detalhes
            "df_otimos":   _df_otimos,
            "limiar_ruim": _limiar_ruim,
            "limiar_otimo":_limiar_otimo,
        })

    _media_global = _df_daily["KG/hora"].mean()

    for _si, _s in enumerate(_stats_meses):
        _positivos = []
        _negativos = []

        # Comparação com mês anterior
        if _si > 0:
            _prev_s = _stats_meses[_si - 1]
            _delta_media = _s["media"] - _prev_s["media"]
            _delta_cv    = _s["cv"]    - _prev_s["cv"]
            if _delta_media > 1:
                _positivos.append(f"média KG/hora subiu **{_delta_media:+.1f}** vs {_prev_s['lbl']}")
            elif _delta_media < -1:
                _negativos.append(f"média KG/hora caiu **{_delta_media:.1f}** vs {_prev_s['lbl']}")
            if _delta_cv < -5:
                _positivos.append(f"ficou **mais consistente** (variação caiu {abs(_delta_cv):.0f}pp)")
            elif _delta_cv > 5:
                _negativos.append(f"ficou **mais irregular** (variação subiu {_delta_cv:.0f}pp)")

        # Comparação com média global
        if _s["media"] > _media_global * 1.05:
            _positivos.append(f"média do mês **acima** da média geral ({_s['media']:.1f} vs {_media_global:.1f} KG/h)")
        elif _s["media"] < _media_global * 0.95:
            _negativos.append(f"média do mês **abaixo** da média geral ({_s['media']:.1f} vs {_media_global:.1f} KG/h)")

        # Dias ruins e ótimos (só contadores para os bullets — detalhes em tabela abaixo)
        if _s["dias_otimos"] > 0:
            _positivos.append(f"**{_s['dias_otimos']} dia(s) excepcional(is)** acima de {_s['limiar_otimo']:.0f} KG/h")
        if _s["dias_ruins"] > 0:
            _negativos.append(f"**{_s['dias_ruins']} dia(s) ruim(ns)** abaixo de {_s['limiar_ruim']:.0f} KG/h — veja detalhes abaixo ↓")

        # Amplitude
        _amplitude = _s["max"] - _s["min"]
        if _amplitude > _s["media"] * 1.2:
            _negativos.append(f"amplitude alta: de **{_s['min']:.1f}** a **{_s['max']:.1f}** KG/h ({_amplitude:.1f} de variação)")

        # CV (coeficiente de variação) absoluto
        if _s["cv"] < 20:
            _positivos.append(f"mês com **boa regularidade** (CV={_s['cv']:.0f}%)")
        elif _s["cv"] > 40:
            _negativos.append(f"mês **muito irregular** (CV={_s['cv']:.0f}%)")

        # Tendência geral do mês
        if _si == len(_stats_meses) - 1 and _si > 0:
            _tend_geral_delta = _stats_meses[-1]["media"] - _stats_meses[0]["media"]
            _tend_geral_pct   = _tend_geral_delta / _stats_meses[0]["media"] * 100
            if _tend_geral_pct > 3:
                _positivos.append(f"**tendência geral positiva:** +{_tend_geral_pct:.1f}% do primeiro ao último mês")
            elif _tend_geral_pct < -3:
                _negativos.append(f"**tendência geral negativa:** {_tend_geral_pct:.1f}% do primeiro ao último mês")

        # Montagem do card de texto
        _emoji_mes = "🥇" if _s["media"] == max(x["media"] for x in _stats_meses) else \
                     "🥉" if _s["media"] == min(x["media"] for x in _stats_meses) else "📅"
        with st.expander(
            f"{_emoji_mes} **{_s['lbl']}** — média {_s['media']:.1f} KG/h · CV {_s['cv']:.0f}% · {_s['dias']} dias",
            expanded=True,
        ):
            _c1, _c2 = st.columns(2)
            with _c1:
                if _positivos:
                    st.markdown("✅ **Pontos positivos**")
                    for _p in _positivos:
                        st.markdown(f"- {_p}")
                else:
                    st.markdown("✅ *Nenhum destaque positivo neste mês.*")
            with _c2:
                if _negativos:
                    st.markdown("⚠️ **Pontos de atenção**")
                    for _n in _negativos:
                        st.markdown(f"- {_n}")
                else:
                    st.markdown("⚠️ *Nenhum ponto de atenção neste mês.*")

            # Tabela de dias ruins com datas e máquinas
            if _s["dias_ruins"] > 0:
                st.markdown(f"**📋 Dias ruins do mês (< {_s['limiar_ruim']:.0f} KG/h):**")
                _df_r = _s["df_ruins"][["Data_dt", "KG/hora", "KG/dia", "Máqs"]].copy()
                _df_r["Data"] = _df_r["Data_dt"].dt.strftime("%d/%m/%Y (%a)").str.replace(
                    "Mon","Seg").str.replace("Tue","Ter").str.replace("Wed","Qua").str.replace(
                    "Thu","Qui").str.replace("Fri","Sex").str.replace("Sat","Sáb").str.replace("Sun","Dom")
                _df_r["KG/hora"] = _df_r["KG/hora"].round(1)
                _df_r["KG/dia"]  = _df_r["KG/dia"].round(1)
                _df_r["vs média"] = (_df_r["KG/hora"] - _s["media"]).round(1).apply(lambda v: f"{v:+.1f}")
                st.dataframe(
                    _df_r[["Data", "Máqs", "KG/hora", "KG/dia", "vs média"]].rename(columns={
                        "Máqs": "Máquina(s)", "vs média": "vs Média (KG/h)"
                    }),
                    use_container_width=True, hide_index=True,
                )

    # ── Tabela resumo mensal ──────────────────────────────────────────────────
    quebra_pagina()
    st.subheader("📋 Tabela resumo mensal")
    st.dataframe(
        _df_monthly.drop(columns=["_ord"]),
        use_container_width=True, hide_index=True,
    )


