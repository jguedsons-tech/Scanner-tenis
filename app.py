import streamlit as st
import requests
from datetime import date, timedelta
import pandas as pd
import numpy as np
import re

st.set_page_config(page_title='Scanner Tênis + Tênis de Mesa', page_icon='🎾', layout='wide')

st.title('🎾🏓 Scanner — Tênis e Tênis de Mesa')
st.caption('Versão sem Sofascore: Tênis via API Tennis; Tênis de Mesa via RapidAPI Table Tennis Live Score.')

# ---------- helpers ----------
def get_secret(name, default=''):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

API_TENNIS_KEY = get_secret('API_TENNIS_KEY')
RAPIDAPI_KEY = get_secret('RAPIDAPI_KEY')


def api_tennis(method, params=None, timeout=20):
    params = dict(params or {})
    params['method'] = method
    params['APIkey'] = API_TENNIS_KEY
    r = requests.get('https://api.api-tennis.com/tennis/', params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get('success') != 1:
        raise RuntimeError(str(data.get('error') or data))
    return data.get('result', [])


def rapid_tt(path, params=None, timeout=20):
    if not RAPIDAPI_KEY:
        raise RuntimeError('RAPIDAPI_KEY não configurada')
    url = 'https://table-tennis-live-score.p.rapidapi.com' + path
    headers = {
        'x-rapidapi-key': RAPIDAPI_KEY,
        'x-rapidapi-host': 'table-tennis-live-score.p.rapidapi.com'
    }
    r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')
    return r.json()


def first_list(obj):
    if isinstance(obj, list): return obj
    if isinstance(obj, dict):
        for k in ('result','results','data','matches','events','response','today','upcoming','recent'):
            v=obj.get(k)
            if isinstance(v,list): return v
        for v in obj.values():
            if isinstance(v,list): return v
    return []


def val(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ''):
            return d[k]
    return None


def parse_players(row):
    a = val(row,'event_first_player','first_player','player1','home_player','home','homeName','player_one','firstPlayer')
    b = val(row,'event_second_player','second_player','player2','away_player','away','awayName','player_two','secondPlayer')
    return str(a or 'Jogador A'), str(b or 'Jogador B')


def parse_result(row):
    w = val(row,'event_winner','winner','winner_id','winnerPlayer')
    final = val(row,'event_final_result','final_result','result','score','event_game_result')
    return w, str(final or '')


def tennis_matches(d):
    rows = api_tennis('get_fixtures', {'date_start':d.isoformat(),'date_stop':d.isoformat(),'timezone':'America/Sao_Paulo'})
    out=[]
    for x in rows if isinstance(rows,list) else []:
        a,b=parse_players(x)
        out.append({
            'id': val(x,'event_key','match_key'),
            'horario': f"{val(x,'event_date',) or d.isoformat()} {val(x,'event_time') or ''}".strip(),
            'competicao': val(x,'tournament_name','event_type_type') or '',
            'jogador_a': a,'jogador_b': b,
            'status': val(x,'event_status','status') or '',
            'resultado': val(x,'event_final_result','event_game_result') or '',
            'raw': x
        })
    return out


def tt_matches():
    # The provider currently advertises a /today endpoint. Keep fallbacks for minor path changes.
    errors=[]
    for path in ['/today','/upcoming','/recent']:
        try:
            obj=rapid_tt(path)
            rows=first_list(obj)
            if rows:
                out=[]
                for x in rows:
                    a,b=parse_players(x)
                    out.append({'id':val(x,'id','match_id','event_id'),'horario':val(x,'time','start_time','date') or '',
                                'competicao':val(x,'tournament','league','competition','event_name') or '',
                                'jogador_a':a,'jogador_b':b,'status':val(x,'status') or '',
                                'resultado':val(x,'score','result') or '', 'raw':x})
                return out, errors
        except Exception as e:
            errors.append(f'{path}: {e}')
    return [], errors


def h2h_analysis(row):
    raw=row['raw']
    p1=val(raw,'first_player_key','player1_id','home_player_id')
    p2=val(raw,'second_player_key','player2_id','away_player_id')
    if not p1 or not p2:
        return {'h2h':0,'a_recent':0,'b_recent':0,'note':'IDs dos jogadores não disponíveis'}
    try:
        r=api_tennis('get_H2H', {'first_player_key':p1,'second_player_key':p2})
        if not isinstance(r,dict): return {'h2h':0,'a_recent':0,'b_recent':0,'note':'Sem H2H'}
        h=r.get('H2H') or []
        ar=r.get('firstPlayerResults') or []
        br=r.get('secondPlayerResults') or []
        def wins(arr, pid):
            w=0;n=0
            for m in arr[:10]:
                n+=1
                winner=val(m,'event_winner','winner','winner_player_key')
                if str(winner)==str(pid): w+=1
                else:
                    # fallback: parse final result only when winner field is absent
                    final=str(val(m,'event_final_result','result') or '')
                    if winner is None and final:
                        left=re.findall(r'\d+',final)
                        if len(left)>=2 and left[0]!=left[1]:
                            # cannot safely map player without side fields
                            pass
            return (w/n if n else 0)
        hwin=wins(h,p1)
        return {'h2h':hwin,'a_recent':wins(ar,p1),'b_recent':wins(br,p2),'note':f'H2H: {len(h)} confrontos'}
    except Exception as e:
        return {'h2h':0,'a_recent':0,'b_recent':0,'note':str(e)[:120]}


def estimate(a_recent,b_recent,h2h=0,rank_a=None,rank_b=None):
    # Descriptive score, not a guarantee. Missing inputs are neutral.
    score=0.50
    score += (a_recent-b_recent)*0.25
    if h2h:
        score += (h2h-0.50)*0.20
    try:
        if rank_a and rank_b and float(rank_a)>0 and float(rank_b)>0:
            # smaller rank is better; bounded adjustment
            diff=np.clip((float(rank_b)-float(rank_a))/1000, -0.20, 0.20)
            score += diff*0.20
    except Exception:
        pass
    score=float(np.clip(score,0.05,0.95))
    return score,1-score

# ---------- sidebar ----------
with st.sidebar:
    st.header('⚙️ Configuração')
    d=st.date_input('Data', value=date.today())
    sport=st.selectbox('Esporte',['🎾 Tênis','🏓 Tênis de mesa'])
    st.divider()
    st.subheader('Fonte')
    st.write('Tênis: API Tennis')
    st.write('Tênis de mesa: RapidAPI')
    if not API_TENNIS_KEY:
        st.warning('API_TENNIS_KEY não configurada')
    if not RAPIDAPI_KEY:
        st.warning('RAPIDAPI_KEY não configurada')

# ---------- main ----------
if sport.startswith('🎾'):
    if not API_TENNIS_KEY:
        st.info('Configure API_TENNIS_KEY nos Secrets do Streamlit Cloud. A API Tennis oferece teste inicial sem cartão e fornece fixtures, H2H e estatísticas.')
    else:
        try:
            matches=tennis_matches(d)
            st.success(f'{len(matches)} partidas encontradas para {d.strftime("%d/%m/%Y")}')
            if matches:
                df=pd.DataFrame([{k:v for k,v in m.items() if k!='raw'} for m in matches])
                st.dataframe(df[['horario','competicao','jogador_a','jogador_b','status','resultado']],use_container_width=True,hide_index=True)
                idx=st.selectbox('Escolha uma partida',range(len(matches)),format_func=lambda i:f"{matches[i]['jogador_a']} x {matches[i]['jogador_b']} — {matches[i]['competicao']}")
                m=matches[idx]
                if st.button('🔎 Analisar partida',type='primary'):
                    with st.spinner('Buscando H2H e forma recente...'):
                        a=h2h_analysis(m)
                    pa,pb=estimate(a['a_recent'],a['b_recent'],a['h2h'])
                    c1,c2,c3=st.columns(3)
                    c1.metric(m['jogador_a'],f'{pa*100:.1f}%')
                    c2.metric(m['jogador_b'],f'{pb*100:.1f}%')
                    c3.metric('Diferença',f'{abs(pa-pb)*100:.1f} p.p.')
                    st.write(f"**Sinal estatístico:** {m['jogador_a'] if pa>pb else m['jogador_b']}")
                    st.caption(a['note'] + '. Probabilidade é estimativa estatística, não garantia.')
        except Exception as e:
            st.error(f'Falha na API Tennis: {e}')
else:
    if not RAPIDAPI_KEY:
        st.info('Configure RAPIDAPI_KEY nos Secrets do Streamlit Cloud. A fonte usa RapidAPI Table Tennis Live Score.')
    else:
        matches,errors=tt_matches()
        if errors:
            with st.expander('Diagnóstico da fonte'):
                st.code('\n'.join(errors))
        if matches:
            st.success(f'{len(matches)} partidas carregadas')
            df=pd.DataFrame([{k:v for k,v in m.items() if k!='raw'} for m in matches])
            st.dataframe(df[['horario','competicao','jogador_a','jogador_b','status','resultado']],use_container_width=True,hide_index=True)
        else:
            st.warning('Nenhuma partida retornada pela fonte de tênis de mesa. O endpoint do provedor pode ter mudado ou o plano pode não incluir dados no momento.')

st.divider()
st.caption('O aplicativo não usa endpoints da Sofascore e não tenta contornar bloqueios. As probabilidades são apenas estimativas estatísticas; não existe garantia de acerto ou lucro.')
