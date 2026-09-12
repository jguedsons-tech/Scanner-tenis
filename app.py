import streamlit as st
import requests, math, statistics
from datetime import datetime, date, timedelta
import pandas as pd

st.set_page_config(page_title='Scanner Tênis & Tênis de Mesa', page_icon='🎾', layout='wide')
BASE='https://api.sofascore.com/api/v1'
HEAD={'User-Agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1','Accept':'application/json'}

@st.cache_data(ttl=60)
def get_json(path):
    r=requests.get(BASE+path,headers=HEAD,timeout=15)
    r.raise_for_status()
    return r.json()

def safe(d,*keys,default=None):
    x=d
    for k in keys:
        if isinstance(x,dict): x=x.get(k)
        else: return default
    return x if x is not None else default

def pct(x): return f'{x*100:.1f}%'

def event_rows(events):
    rows=[]
    for e in events:
        h=safe(e,'homeTeam','name',default='?'); a=safe(e,'awayTeam','name',default='?')
        hs=safe(e,'homeScore','current'); aws=safe(e,'awayScore','current')
        rows.append({'id':e.get('id'),'inicio':datetime.fromtimestamp(e.get('startTimestamp',0)).strftime('%d/%m %H:%M') if e.get('startTimestamp') else '', 'mandante':h,'visitante':a,'placar':f'{hs}-{aws}' if hs is not None and aws is not None else '-', 'status':safe(e,'status','description',default='')})
    return rows

def get_events(sport, d):
    if sport=='Tênis':
        data=[]
        for page in range(1,4):
            try:
                j=get_json(f'/sport/tennis/scheduled-tournaments/{d}/page/{page}')
                # scheduled-tournaments returns tournaments with events nested differently depending on version
                for t in j.get('groups', j.get('tournaments', [])):
                    data += t.get('events',[]) if isinstance(t,dict) else []
                if not data:
                    for t in j.get('tournaments',[]): data += t.get('events',[]) if isinstance(t,dict) else []
            except Exception: pass
        # fallback to generic scheduled events if available
        if not data:
            try: data=get_json(f'/sport/tennis/events/{d}').get('events',[])
            except Exception: data=[]
        return data
    else:
        try: return get_json(f'/sport/table-tennis/scheduled-events/{d}').get('events',[])
        except Exception:
            try: return get_json(f'/sport/table-tennis/events/{d}').get('events',[])
            except Exception: return []

def event_detail(eid):
    return get_json(f'/event/{eid}').get('event',{})

def event_stats(eid):
    try: return get_json(f'/event/{eid}/statistics')
    except Exception: return {}

def extract_recent_player_matches(player_id, limit=10):
    for p in [0,1]:
        try:
            j=get_json(f'/player/{player_id}/events/last/{p}')
            ev=j.get('events',[])
            if ev: return ev[:limit]
        except Exception: pass
    return []

def player_form(player_id, limit=10):
    ev=extract_recent_player_matches(player_id,limit)
    wins=0; played=0; sets_for=sets_against=0; points_for=points_against=0
    for e in ev:
        h=safe(e,'homeTeam','id'); a=safe(e,'awayTeam','id')
        if player_id not in (h,a): continue
        hp=safe(e,'homeScore','current'); ap=safe(e,'awayScore','current')
        if hp is None or ap is None: continue
        played+=1; ishome=(h==player_id)
        if (ishome and hp>ap) or ((not ishome) and ap>hp): wins+=1
        hs=safe(e,'homeScore','period1'); as_=safe(e,'awayScore','period1')
        if hs is not None and as_ is not None:
            if ishome: sets_for+=hs; sets_against+=as_
            else: sets_for+=as_; sets_against+=hs
    return {'played':played,'wins':wins,'winrate':wins/played if played else 0.5,'sets_for':sets_for,'sets_against':sets_against}

def probability_from_form(hf,af):
    # conservative blend: form 55%, baseline 45%; never claims certainty
    p=.55*hf['winrate']+.45*.5
    q=.55*af['winrate']+.45*.5
    s=p+q
    return p/s if s else .5

st.title('🎾🏓 Scanner de Tênis & Tênis de Mesa')
st.caption('Análise estatística de jogos reais. As probabilidades são estimativas do modelo, não garantias.')

with st.sidebar:
    sport=st.selectbox('Esporte',['Tênis','Tênis de mesa'])
    d=st.date_input('Data',date.today())
    limit=st.slider('Jogos a analisar',10,100,30)
    if st.button('🔄 Atualizar jogos',use_container_width=True): st.cache_data.clear()

try:
    events=get_events(sport,d.isoformat())
except Exception as ex:
    events=[]
    st.error(f'Falha ao consultar a fonte: {ex}')

if not events:
    st.warning('Nenhum jogo foi carregado. A fonte pública pode estar indisponível ou ter alterado o endpoint. O app inclui tratamento para isso.')
else:
    rows=event_rows(events)
    df=pd.DataFrame(rows)
    st.subheader(f'Jogos encontrados: {len(df)}')
    st.dataframe(df[['inicio','mandante','visitante','placar','status']],use_container_width=True,hide_index=True)

    options={f"{r['mandante']} x {r['visitante']} — {r['inicio']}":r['id'] for r in rows if r['id']}
    if options:
        selected=st.selectbox('Analisar partida',list(options))
        eid=options[selected]
        if st.button('📊 Analisar partida',type='primary'):
            with st.spinner('Buscando histórico e estatísticas...'):
                try:
                    e=event_detail(eid)
                    hid=safe(e,'homeTeam','id'); aid=safe(e,'awayTeam','id')
                    hf=player_form(hid,limit) if hid else {'played':0,'wins':0,'winrate':.5,'sets_for':0,'sets_against':0}
                    af=player_form(aid,limit) if aid else {'played':0,'wins':0,'winrate':.5,'sets_for':0,'sets_against':0}
                    p=probability_from_form(hf,af)
                    cols=st.columns(4)
                    cols[0].metric(safe(e,'homeTeam','name',default='Casa'),pct(p))
                    cols[1].metric('Empate','—')
                    cols[2].metric(safe(e,'awayTeam','name',default='Fora'),pct(1-p))
                    cols[3].metric('Amostra',f"{hf['played']} x {af['played']}")
                    c1,c2=st.columns(2)
                    with c1:
                        st.markdown('### Forma recente')
                        st.write(f"**{safe(e,'homeTeam','name')}**: {hf['wins']}/{hf['played']} vitórias ({pct(hf['winrate'])})")
                        st.write(f"**{safe(e,'awayTeam','name')}**: {af['wins']}/{af['played']} vitórias ({pct(af['winrate'])})")
                    with c2:
                        st.markdown('### Sinal do modelo')
                        if p>=.60: st.success(f"JOGADA SUGERIDA: {safe(e,'homeTeam','name')} — sinal estatístico")
                        elif p<=.40: st.success(f"JOGADA SUGERIDA: {safe(e,'awayTeam','name')} — sinal estatístico")
                        else: st.info('Sem vantagem estatística suficiente no modelo.')
                    stats=event_stats(eid)
                    with st.expander('Estatísticas da partida'):
                        st.json(stats)
                except Exception as ex:
                    st.error(f'Não foi possível analisar esta partida: {ex}')

st.divider()
st.info('Fonte principal nesta versão: endpoints públicos usados pelo ecossistema Sofascore. A própria Sofascore informa que não oferece seus dados como API pública oficial; portanto esta integração pode mudar ou deixar de funcionar sem aviso. Para produção, o ideal é substituir por um provedor licenciado/oficial.')
