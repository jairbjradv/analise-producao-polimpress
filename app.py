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
> 📄 **Relatório necessário:** utilize o **RPCP621 — Relatório para Análise de Produção** para gerar os PDFs compatíveis com este sistema.

- 🏆 **Ranking de Operadores** — compara todos os operadores por **KG/hora** real trabalhada, eliminando a diferença de turno, sábados e dias trabalhados. Também detalha o desempenho de cada operador dentro de cada máquina que utilizou.
- 🏭 **Ranking por Máquina** — mostra quais máquinas mais produziram (KG/dia e UN/dia), qual operador foi o melhor em cada uma e permite comparar lado a lado os operadores de uma mesma máquina.
- ⚖️ **Comparativo por Item** — selecione um produto e veja quem produziu mais (KG/hora) naquele item específico — comparação justa, mesma matéria-prima e mesmo produto.
- 🏅 **Top Produção** — ranking de quem produziu mais em volume total (KG e UN), tanto por operador quanto por máquina.
- 📋 **Dados Brutos** — todos os registros extraídos dos PDFs com filtros por operador, turno e máquina, exportável em CSV.
""")

_RE_ITEM_UN     = re.compile(r"^(\d+)\s*-\s*(.*)\s+([\d\.]+,\d+)\s*UN\s+([\d\.]+,\d+)\s*$")
_RE_ITEM_KG     = re.compile(r"^(\d+)\s*-\s*(.*)\s+([\d\.]+,\d+)\s*KG\s+([\d\.]+,\d+)\s*$")
_RE_USUARIO     = re.compile(r"[Uu]su.{0,2}rio:\s*\d+\s*-\s*(.+)")
_RE_FUNCIONARIO = re.compile(r"^Funcion.{0,2}rio:\s*\d+\s*-\s*(.+)")
_RE_TURNO       = re.compile(r"Turno:\s*(\d+)")
_RE_DIA         = re.compile(r"Dia:\s*(\d{2}/\d{2}/\d{4})")
_RE_RECURSO     = re.compile(r"Recurso:\s*(.+)")
_RE_PERIODO     = re.compile(r"Per.{0,2}odo:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})(?:.*?Turno\*:\s*(\d+))?")

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
    _SKIP = (
        "Total de Registros", "Total Funcion", "Total Turno", "Total Recurso",
        "Item QuantidadeUnidade", "POLIMPRESS", "CNPJ", "DRACENA",
        "Primeira Quebra", "Segunda Quebra", "Terceira Quebra", "Agrupamento",
        "Tipo Recurso", "Local Busca", "ROTINA:", "HORA:", "PÁGINA:", "INSCR.",
        "Análise Resumida", "Análise De", "REBOBINAGEM", "CORTE E SOLDA",
    )
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
                if any(k in linha for k in _SKIP):
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

                m = _RE_ITEM_UN.match(linha) or _RE_ITEM_KG.match(linha)
                if not m:
                    continue

                unidade = "UN" if _RE_ITEM_UN.match(linha) else "KG"
                try:
                    quantidade = _parse_br_float(m.group(3))
                    peso       = _parse_br_float(m.group(4))
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
    "Arraste e solte todos os PDFs de produção aqui de uma vez",
    type=["pdf"],
    accept_multiple_files=True,
)

if not arquivos_pdf:
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
df["Data_dt"]          = pd.to_datetime(df["Data"], format="%d/%m/%Y")
df["Periodo_Inicio_dt"] = pd.to_datetime(df["Periodo_Inicio"], format="%d/%m/%Y", errors="coerce")
df["Periodo_Fim_dt"]    = pd.to_datetime(df["Periodo_Fim"],    format="%d/%m/%Y", errors="coerce")

st.success(
    f"✅ {len(arquivos_pdf)} relatório(s) · {len(df)} registros · "
    f"{df['Operador'].nunique()} operador(es)"
)

aba1, aba2, aba3, aba4, aba5, aba6 = st.tabs([
    "🏆 Ranking de Operadores",
    "🏭 Ranking por Máquina",
    "⚖️ Comparativo por Item",
    "🏅 Top Produção",
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

    r = (
        df_in.groupby("Operador")
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Total_UN=("Qtd (UN)", "sum"),
            Apontamentos=("Cód Item", "count"),
            Produtos_Distintos=("Cód Item", "nunique"),
            Peso_Medio_g=("Peso Médio/UN (g)", "mean"),
        )
        .join(turno_por_op).join(nome_curto_op)
        .reset_index()
    )
    r["Dias Trabalhados"]    = r["Operador"].map(dias_op)
    r["Horas Trabalhadas"]   = r["Operador"].map(horas_op).round(1)
    r["KG / Hora"]           = (r["Total_KG"] / r["Horas Trabalhadas"]).round(2)
    r["UN / Hora"]           = (r["Total_UN"] / r["Horas Trabalhadas"]).round(1)
    r["KG / Dia"]            = (r["Total_KG"] / r["Dias Trabalhados"]).round(1)
    r["Apontamentos / Dia"]  = (r["Apontamentos"] / r["Dias Trabalhados"]).round(1)
    r["Peso Médio/peça (g)"] = r["Peso_Medio_g"].round(2)
    return r.sort_values("KG / Hora", ascending=False).reset_index(drop=True)


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

    resumo = calcular_resumo_operadores(df)

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
            df_m.groupby(["Operador", "Nome Curto"])
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

            _top3_m = min(n_m, 3)
            cols_m = st.columns(_top3_m)
            for i in range(_top3_m):
                row = r_m.iloc[i]
                with cols_m[i]:
                    st.metric(
                        label=f"{_medalhas[i]} {row['Nome Curto']}",
                        value=f"{row['KG / Hora']:.2f} KG/hora",
                        delta=f"{row['Dias']} dias · {row['Horas']:.0f}h",
                    )
            if n_m > 3:
                with st.expander(f"Ver todos os {n_m} operadores"):
                    _rest_m = r_m.iloc[3:]
                    _cols_rest = st.columns(min(len(_rest_m), 5))
                    for j, (_, row) in enumerate(_rest_m.iterrows()):
                        with _cols_rest[j % 5]:
                            st.metric(
                                label=row["Nome Curto"],
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


# ── Aba 5: Resumo Geral ──────────────────────────────────────────────────────
with aba5:
    st.header("📊 Resumo Geral de Produção")
    st.caption("Soma total produzida no período analisado, por operador e por máquina.")

    # ── Tabela de Operadores ──────────────────────────────────────────────────
    st.subheader("👷 Produção por Operador")

    op_resumo = (
        df.groupby(["Operador", "Nome Curto", "Turno"])
        .agg(
            Total_KG=("Peso (KG)", "sum"),
            Total_UN=("Qtd (UN)", "sum"),
            Apontamentos=("Cód Item", "count"),
        )
        .reset_index()
        .sort_values("Total_KG", ascending=False)
        .reset_index(drop=True)
    )
    op_resumo.index = op_resumo.index + 1  # começa em 1

    # Linha de total
    total_op = pd.DataFrame([{
        "Operador": "─── TOTAL ───",
        "Nome Curto": "TOTAL",
        "Turno": "",
        "Total_KG": op_resumo["Total_KG"].sum(),
        "Total_UN": op_resumo["Total_UN"].sum(),
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
            "Total_UN": "Total UN",
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
            Total_UN=("Qtd (UN)", "sum"),
            Apontamentos=("Cód Item", "count"),
            Operadores=("Nome Curto", lambda x: len(x.unique())),
        )
        .reset_index()
        .sort_values("Total_KG", ascending=False)
        .reset_index(drop=True)
    )
    maq_resumo.index = maq_resumo.index + 1

    total_maq = pd.DataFrame([{
        "Máquina": "─── TOTAL ───",
        "Total_KG": maq_resumo["Total_KG"].sum(),
        "Total_UN": maq_resumo["Total_UN"].sum(),
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
            "Total_UN": "Total UN",
            "Operadores": "Nº Operadores",
        }),
        use_container_width=True,
    )

    # ── Totalizador geral ─────────────────────────────────────────────────────
    quebra_pagina()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⚖️ Total KG", f"{df['Peso (KG)'].sum():,.0f} KG")
    c2.metric("🔢 Total UN", f"{df['Qtd (UN)'].sum():,.0f} UN")
    c3.metric("👷 Operadores", df["Operador"].nunique())
    c4.metric("🏭 Máquinas", df["Máquina"].nunique())


# ── Aba 6: Dados Brutos ───────────────────────────────────────────────────────
with aba6:
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
