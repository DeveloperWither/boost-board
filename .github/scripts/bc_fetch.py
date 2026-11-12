"""Boost Board: pull each linked player's recent ranked replays from ballchasing.com and write a stats file per player.

Runs in GitHub Actions (see .github/workflows/ballchasing.yml). Needs the BALLCHASING_KEY secret.
Players link their ballchasing profile in the app; links are read from the Firebase database (bclink/<player>).
Output: bc/<player>.json, read by the app. bc/cache.json keeps the numbers we need from replays already fetched,
so each replay is only downloaded once (ballchasing limits: 2 calls/second, 500 lists and 1000 replays per hour).
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error

API = 'https://ballchasing.com/api'
DB = 'https://boost-board-rl26-default-rtdb.firebaseio.com'
OUT = os.path.join(os.path.dirname(__file__), '..', '..', 'bc')
KEY = os.environ.get('BALLCHASING_KEY', '').strip()
PLAYLISTS = {'ranked-duels': '1', 'ranked-doubles': '2', 'ranked-standard': '3'}
PER_PLAYER = 15
MAX_CACHE = 3000

# Per-player numbers kept from each replay: (stats group, field)
FIELDS = [
    ('positioning', 'percent_behind_ball'), ('positioning', 'percent_most_back'), ('positioning', 'percent_most_forward'),
    ('positioning', 'percent_closest_to_ball'), ('positioning', 'percent_defensive_third'), ('positioning', 'percent_offensive_third'),
    ('positioning', 'goals_against_while_last_defender'), ('positioning', 'avg_distance_to_ball'),
    ('boost', 'bpm'), ('boost', 'avg_amount'), ('boost', 'percent_zero_boost'), ('boost', 'percent_full_boost'),
    ('boost', 'count_collected_small'), ('boost', 'count_collected_big'), ('boost', 'amount_stolen'), ('boost', 'amount_overfill'),
    ('movement', 'avg_speed'), ('movement', 'percent_supersonic_speed'), ('movement', 'percent_slow_speed'),
    ('movement', 'percent_ground'), ('movement', 'percent_high_air'), ('movement', 'count_powerslide'),
    ('core', 'shots'), ('core', 'goals'), ('core', 'saves'), ('core', 'assists'), ('core', 'score'),
    ('core', 'shooting_percentage'), ('core', 'goals_against'),
    ('demo', 'inflicted'), ('demo', 'taken'),
]

_last = [0.0]
def api(path, params=None):
    url = API + path + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
    for attempt in range(5):
        wait = 0.6 - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={'Authorization': KEY, 'User-Agent': 'boost-board-stats'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1)); continue
            raise
    raise RuntimeError('rate limited: ' + path)

def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

def extract(d):
    """Keep only what we need from a full replay."""
    out = {'date': d.get('date'), 'mode': PLAYLISTS.get(d.get('playlist_id'), '?'), 'players': []}
    goals = {}
    for side in ('blue', 'orange'):
        t = d.get(side) or {}
        goals[side] = t.get('goals', 0) or 0
        for p in t.get('players') or []:
            st = p.get('stats') or {}
            m = {}
            for grp, f in FIELDS:
                v = (st.get(grp) or {}).get(f)
                if isinstance(v, (int, float)): m[f] = round(v, 2)
            pid = p.get('id') or {}
            out['players'].append({'n': p.get('name', ''), 'p': pid.get('platform', ''), 'i': str(pid.get('id', '')), 't': side, 'm': m})
    out['blue'], out['orange'] = goals.get('blue', 0), goals.get('orange', 0)
    return out

def is_me(pl, link):
    if link.get('p') and link.get('i'):
        return pl['p'] == link['p'] and pl['i'] == link['i']
    return pl['n'].lower() == (link.get('n') or '').lower()

def summarize(games, link):
    rows, mine, lobby = [], {}, {}
    for g in games:
        me = next((p for p in g['players'] if is_me(p, link)), None)
        if not me: continue
        others = [p for p in g['players'] if p is not me]
        my_goals = g['blue'] if me['t'] == 'blue' else g['orange']
        their_goals = g['orange'] if me['t'] == 'blue' else g['blue']
        rows.append({'date': g['date'], 'mode': g['mode'], 'win': my_goals > their_goals, 'score': f'{my_goals}-{their_goals}',
                     'goals': me['m'].get('goals', 0), 'saves': me['m'].get('saves', 0), 'assists': me['m'].get('assists', 0),
                     'behind': me['m'].get('percent_behind_ball'), 'zero': me['m'].get('percent_zero_boost')})
        for _, f in FIELDS:
            if f in me['m']:
                mine.setdefault(f, []).append(me['m'][f])
            vals = [o['m'][f] for o in others if f in o['m']]
            if vals: lobby.setdefault(f, []).append(sum(vals) / len(vals))
    avg = lambda a: round(sum(a) / len(a), 2) if a else None
    return {'games': len(rows), 'wins': sum(1 for r in rows if r['win']), 'recent': rows,
            'me': {f: avg(v) for f, v in mine.items()}, 'lobby': {f: avg(v) for f, v in lobby.items()}}

def main():
    if not KEY:
        print('BALLCHASING_KEY is not set yet: nothing to do'); return
    os.makedirs(OUT, exist_ok=True)
    cache_path = os.path.join(OUT, 'cache.json')
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    links = get_json(DB + '/bclink.json') or {}
    print(f'{len(links)} linked player(s)')
    for uid, link in links.items():
        q = {'playlist': list(PLAYLISTS), 'count': PER_PLAYER, 'sort-by': 'replay-date', 'sort-dir': 'desc'}
        if link.get('p') and link.get('i'): q['player-id'] = f"{link['p']}:{link['i']}"
        elif link.get('n'): q['player-name'] = link['n']
        else: continue
        try:
            lst = api('/replays', q).get('list') or []
        except Exception as e:
            print(f'{uid}: list failed: {e}'); continue
        games = []
        for r in lst:
            rid = r['id']
            if rid not in cache:
                try: cache[rid] = extract(api('/replays/' + rid))
                except Exception as e: print(f'  replay {rid} failed: {e}'); continue
            games.append(cache[rid])
        s = summarize(games, link)
        s.update({'updated': int(time.time() * 1000), 'link': link})
        with open(os.path.join(OUT, uid + '.json'), 'w') as f: json.dump(s, f, separators=(',', ':'))
        print(f'{uid}: {s["games"]} games')
    # Players who unlinked: remove their file.
    for fn in os.listdir(OUT):
        if fn.endswith('.json') and fn != 'cache.json' and fn[:-5] not in links:
            os.remove(os.path.join(OUT, fn))
    if len(cache) > MAX_CACHE:
        keep = sorted(cache, key=lambda k: cache[k].get('date') or '')[-MAX_CACHE:]
        cache = {k: cache[k] for k in keep}
    with open(cache_path, 'w') as f: json.dump(cache, f, separators=(',', ':'))

if __name__ == '__main__':
    main()
