import streamlit as st
import math
from datetime import datetime, date
import pandas as pd

try:
    from curl_cffi import requests as http
    CURL_CFFI = True
except Exception:
    import requests as http
    CURL_CFFI = False

st.set_page_config(page_title='Scanner Tênis & Tênis de Mesa', page_icon='🎾', layout='wide')

BASES = [
    'https://api.sofascore.com/api/v1',
    'https://api.sofascore.app/api/v1',
    'https://www.sofascore.com/api/v1',
]

HEAD = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
    'Referer': 'https://www.sofascore.com/',
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.7 Mobile/15E148 Safari/604.1',
}


def request_json(path):
    errors = []
    for base in BASES:
        url = base + path
        try:
            if CURL_CFFI:
                r = http.get(url, headers=HEAD, timeout=20, impersonate='safari_ios')
            else:
                r = http.get(url, headers=HEAD, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
                errors.append(f'{url}: resposta não é JSON objeto')
            else:
                errors.append(f'{url}: HTTP {r.status_code}')
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__}: {exc}')
    raise RuntimeError(' | '.join(errors[-4:]))


@st.cache_data(ttl=90, show_spinner=False)
def get_json(path):
    return request_json(path)


def safe(d, *keys, default=None):
    x = d
    for k in keys:
        if isinstance(x, dict):
            x = x.get(k)
        else:
            return default
    return default if x is None else x


def pct(x):
    return f'{x*100:.1f}%'


def flatten_events(obj):
    """Extract events from several known Sofascore schedule shapes."""
    if isinstance(obj, dict):
        if isinstance(obj.get('events'), list):
            for e in obj['events']:
                if isinstance(e, dict) and e.get('id'):
                    yield e
        for key in ('groups', 'tournaments', 'eventsGroups'):
            val = obj.get(key)
            if isinstance(val, list):
                for item in val:
                    yield from flatten_events(item)
            elif isinstance(val, dict):
                yield from flatten_events(val)
    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_events(item)


def get_events(sport, d):
    found = {}
    paths = []
    if sport == 'Tênis':
        # Tennis currently uses scheduled-tournaments with pagination; the old
        # scheduled-events route has been reported as unstable/404.
        for page in range(1, 8):
            paths.append(f'/sport/tennis/scheduled-tournaments/{d}/page/{page}')
    else:
        # Table tennis normally follows the generic scheduled-events route.
        paths.extend([
            f'/sport/table-tennis/scheduled-events/{d}',
            f'/sport/table-tennis/events/{d}',
        ])

    last_errors = []
    for path in paths:
        try:
            payload = get_json(path)
            for e in flatten_events(payload):
                found[e['id']] = e
            if sport == 'Tênis' and len(found) > 0:
                # pages are chronological/grouped; keep loading a few pages,
                # but stop after a page that returns no new events.
                continue
            if sport != 'Tênis' and found:
                break
        except Exception as exc:
            last_errors.append(str(exc))
            continue

    events = list(found.values())
    if not events and last_errors:
        raise RuntimeError(last_errors[-1])
    return events


def event_rows(events):
    rows = []
    for e in events:
        h = safe(e, 'homeTeam', 'name', default='?')
        a = safe(e, 'awayTeam', 'name', default='?')
        hs = safe(e, 'homeScore', 'current')
        aws = safe(e, 'awayScore', 'current')
        ts = e.get('startTimestamp')
        rows.append({
            'id': e.get('id'),
            'inicio': datetime.fromtimestamp(ts).strftime('%d/%m %H:%M') if ts else '',
            'mandante': h,
            'visitante': a,
            'placar': f'{hs}-{aws}' if hs is not None and aws is not None else '-',
            'status': safe(e, 'status', 'description', default=''),
            'torneio': safe(e, 'tournament', 'name', default=''),
        })
    return rows


def event_detail(eid):
    return get_json(f'/event/{eid}').get('event', {})


def event_stats(eid):
    try:
        return get_json(f'/event/{eid}/statistics')
    except Exception:
        return {}


def extract_recent_player_matches(player_id, limit=20):
    # Current and legacy forms.
    for path in [f'/player/{player_id}/events/last/0', f'/player/{player_id}/events/last']:
        try:
            j = get_json(path)
            ev = j.get('events', [])
            if ev:
                return ev[:limit]
        except Exception:
            pass
    return []


def player_form(player_id, limit=20):
    ev = extract_recent_player_matches(player_id, limit)
    wins = played = 0
    sets_for = sets_against = 0
    for e in ev:
        h = safe(e, 'homeTeam', 'id')
        a = safe(e, 'awayTeam', 'id')
        if player_id not in (h, a):
            continue
        hp = safe(e, 'homeScore', 'current')
        ap = safe(e, 'awayScore', 'current')
        if hp is None or ap is None:
            continue
        played += 1
        ishome = h == player_id
        if (ishome and hp > ap) or ((not ishome) and ap > hp):
            wins += 1
        # For tennis, current is usually sets; period1/2/... are individual sets.
        for period in ('period1', 'period2', 'period3', 'period4', 'period5'):
            hs = safe(e, 'homeScore', period)
            aws = safe(e, 'awayScore', period)
            if hs is None or aws is None:
                continue
            if ishome:
                sets_for += int(hs > aws)
                sets_against += int(aws > hs)
            else:
                sets_for += int(aws > hs)
                sets_against += int(hs > aws)
    return {
        'played': played,
        'wins': wins,
        'winrate': wins / played if played else 0.5,
        'sets_for': sets_for,
        'sets_against': sets_against,
    }


def probability_from_form(hf, af):
    # Conservative descriptive model; not a guarantee.
    p = .55 * hf['winrate'] + .45 * .5
    q = .55 * af['winrate'] + .45 * .5
    s = p + q
    return p / s if s else .5


st.title('🎾🏓 Scanner de Tênis & Tênis de Mesa')
st.caption('Dados de partidas e histórico; probabilidades são estimativas estatísticas, não garantias.')

with st.sidebar:
    sport = st.selectbox('Esporte', ['Tênis', 'Tênis de mesa'])
    d = st.date_input('Data', date.today())
    limit = st.slider('Histórico por jogador', 10, 50, 20)
    if st.button('🔄 Atualizar jogos', use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with st.expander('Diagnóstico da fonte'):
    st.write('Cliente TLS:', 'curl_cffi (Safari iOS)' if CURL_CFFI else 'requests')
    st.write('Fontes de fallback:', ', '.join(BASES))

try:
    events = get_events(sport, d.isoformat())
except Exception as ex:
    events = []
    st.error('A fonte respondeu com erro. O scanner tentou múltiplos endpoints/domínios.')
    with st.expander('Detalhes técnicos'):
        st.code(str(ex))

if not events:
    st.warning('Nenhum jogo foi carregado para essa data. Tente Atualizar ou outra data.')
else:
    rows = event_rows(events)
    df = pd.DataFrame(rows).sort_values('inicio')
    st.subheader(f'Jogos encontrados: {len(df)}')
    st.dataframe(df[['inicio', 'mandante', 'visitante', 'torneio', 'placar', 'status']], use_container_width=True, hide_index=True)

    options = {
        f"{r['mandante']} x {r['visitante']} — {r['inicio']}": r['id']
        for r in rows if r['id']
    }
    if options:
        selected = st.selectbox('Analisar partida', list(options))
        eid = options[selected]
        if st.button('📊 Analisar partida', type='primary'):
            with st.spinner('Buscando histórico e estatísticas...'):
                try:
                    e = event_detail(eid)
                    hid = safe(e, 'homeTeam', 'id')
                    aid = safe(e, 'awayTeam', 'id')
                    hf = player_form(hid, limit) if hid else {'played':0,'wins':0,'winrate':.5,'sets_for':0,'sets_against':0}
                    af = player_form(aid, limit) if aid else {'played':0,'wins':0,'winrate':.5,'sets_for':0,'sets_against':0}
                    p = probability_from_form(hf, af)
                    home = safe(e, 'homeTeam', 'name', default='Casa')
                    away = safe(e, 'awayTeam', 'name', default='Fora')
                    cols = st.columns(3)
                    cols[0].metric(home, pct(p))
                    cols[1].metric('Amostra', f"{hf['played']} x {af['played']}")
                    cols[2].metric(away, pct(1-p))
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown('### Forma recente')
                        st.write(f'**{home}**: {hf["wins"]}/{hf["played"]} vitórias ({pct(hf["winrate"])})')
                        st.write(f'**{away}**: {af["wins"]}/{af["played"]} vitórias ({pct(af["winrate"])})')
                        st.write(f'Sets ganhos: {hf["sets_for"]} × {af["sets_for"]}')
                    with c2:
                        st.markdown('### Sinal do modelo')
                        if p >= .60:
                            st.success(f'JOGADA SUGERIDA: {home} — sinal estatístico')
                        elif p <= .40:
                            st.success(f'JOGADA SUGERIDA: {away} — sinal estatístico')
                        else:
                            st.info('Sem vantagem estatística suficiente no modelo.')
                    stats = event_stats(eid)
                    with st.expander('Estatísticas da partida'):
                        st.json(stats if stats else {'info': 'Estatísticas detalhadas não disponíveis para este evento.'})
                except Exception as ex:
                    st.error(f'Não foi possível analisar esta partida: {ex}')

st.divider()
st.info('A integração usa endpoints não oficiais do ecossistema Sofascore e possui múltiplos fallbacks. A Sofascore informa que não fornece suas fontes de dados como API pública oficial; endpoints podem mudar. Para uso estável em produção, prefira um provedor licenciado.')
