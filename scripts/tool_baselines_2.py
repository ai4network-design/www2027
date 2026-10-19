#!/usr/bin/env python3
"""Second set of published-tool baselines for the compile benchmarks (WWW 2027 paper; contract contracts/tool_baselines_2_20261019.json).

time   heideltime  HeidelTime (standalone jar via py_heideltime, TreeTagger bundled); TIMEX3 values as SUTime: a TIME value
                   'YYYY-MM-DDTHH:MM' or 'XXXX-XX-XXTHH:MM' (undated clock -> posting date), a DATE value 'YYYY-MM-DD'.
                   The same raw / glue conversions as scripts/sota_baselines.py (the tool finds the expressions; glue only
                   attaches the nearest written date and the zone token written after the clock). HeidelTime's English
                   rules tag am/pm clocks but not bare 24-hour clocks ('21:00 UTC'); kept as published.
       cmp_rules   Network to Code circuit-maintenance-parser 2.12 as published without an LLM: every registered provider
                   class is tried on the notice as text/plain (title as the e-mail subject, posting time as the e-mail date);
                   the first provider that returns a Maintenance gives start/end (epoch, UTC) and status.
       cmp_llm     the same library's LLM fallback (GenericProvider + OpenAIParser, the library's own prompt), model from
                   PARSER_OPENAI_MODEL; needs PARSER_OPENAI_API_KEY. One call per item.
       cmp_llm_local  the same fallback path and prompt, but the chat call served by a local open-weight model (--local-model,
                   an asset name such as qwen7b) so the arm stays reproducible; greedy decoding, one call per item.
       For revision posts the notification text is announcement + update (the information the model arms get); the action
       is CANCELLED -> cancelled, RE-SCHEDULED -> moved, otherwise moved if a start is returned that differs from the
       announced start by more than the tolerance, extended if only the end differs, else unchanged.

    PYTHONPATH=src python scripts/tool_baselines_2.py time --tool heideltime --part dev --target ann --out runs/tool_baselines_2_20261019/time
    PYTHONPATH=src python scripts/tool_baselines_2.py time --tool cmp_rules --part dev --target upd --out runs/tool_baselines_2_20261019/time
    PARSER_OPENAI_API_KEY=... PARSER_OPENAI_MODEL=gpt-3.5-turbo PYTHONPATH=src python scripts/tool_baselines_2.py time --tool cmp_llm --part dev --target ann --out ...
"""
import argparse, datetime as D, email.utils, importlib.util, json, logging, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_b = importlib.util.spec_from_file_location('sb', HERE / 'sota_baselines.py'); SB = importlib.util.module_from_spec(_b); _b.loader.exec_module(SB)
mce, RCE, window_from, DATEISH = SB.mce, SB.RCE, SB.window_from, SB.DATEISH
from vera import multiservice_control as m
logging.getLogger('circuit_maintenance_parser').setLevel(logging.CRITICAL)


def expressions_heideltime(text, posted):
    import py_heideltime.py_heideltime as H
    ref = D.datetime.fromisoformat(posted); out = []
    for x in H.heideltime(text, language='English', document_type='news', dct=posted[:10]):
        v = str(x.get('value', '')); pos = x['span'][0] if x.get('span') else text.find(x['text'])
        mt = re.match(r'^(\d{4}-\d{2}-\d{2}|XXXX-XX-XX|\d{4}-\d{2}-XX|\d{4}-XX-XX)T(\d{2}:\d{2})', v)
        if mt:
            date = mt.group(1) if 'X' not in mt.group(1) else ref.strftime('%Y-%m-%d')
            dt = D.datetime.fromisoformat(date + 'T' + mt.group(2))
            in_text = bool(DATEISH.search(x['text'])) or bool(re.search(r'20\d{2}', x['text']))
            out.append(dict(text=x['text'], pos=pos, dt=dt, off=None, zoned=False, clock=True, date=in_text and 'X' not in mt.group(1)))
            continue
        md = re.match(r'^(\d{4}-\d{2}-\d{2})$', v)
        if md:
            out.append(dict(text=x['text'], pos=pos, dt=D.datetime.fromisoformat(md.group(1)), off=None, zoned=False, clock=False, date=True))
    return out


def _notification(text, title, posted):
    from circuit_maintenance_parser import NotificationData
    from circuit_maintenance_parser.constants import EMAIL_HEADER_DATE, EMAIL_HEADER_SUBJECT
    nd = NotificationData.init_from_raw('text/plain', text.encode())
    stamp = D.datetime.fromisoformat(posted).replace(tzinfo=D.timezone.utc)
    nd.add_data_part(EMAIL_HEADER_DATE, email.utils.format_datetime(stamp).encode())
    nd.add_data_part(EMAIL_HEADER_SUBJECT, (title or 'Scheduled maintenance').encode())
    return nd


def _providers():
    import inspect
    from circuit_maintenance_parser import provider as P
    names = [n for n, c in inspect.getmembers(P, inspect.isclass) if issubclass(c, P.GenericProvider) and n != 'GenericProvider']
    return [('GenericProvider', P.GenericProvider)] + [(n, getattr(P, n)) for n in sorted(names)]


_LOCAL = {}


def local_llm_parser(model):
    """The library's LLM parser with its own prompt and JSON handling, but the chat call served by a local open-weight model
    (scripts/offline_compile_eval.Chat on the project's model assets) instead of the OpenAI client. Greedy decoding, one call per
    notification; the library's prompt asks for start/end 'displayed in UTC', so the model converts the zone itself (the
    library's design, kept as is)."""
    from circuit_maintenance_parser.parsers.openai import OpenAIParser
    if 'chat' not in _LOCAL:
        _o = importlib.util.spec_from_file_location('oce', HERE / 'offline_compile_eval.py'); oce = importlib.util.module_from_spec(_o); _o.loader.exec_module(oce)
        assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
        _LOCAL['chat'] = oce.Chat(assets[model]['directory'], None, quant=assets[model].get('quant')); _LOCAL['calls'] = 0

    class LocalLLMParser(OpenAIParser):
        def get_llm_response(self, content):
            raw, _ms = _LOCAL['chat']([dict(role='system', content=self.llm_question), dict(role='user', content=content)], max_new_tokens=640)
            _LOCAL['calls'] += 1; txt = raw.strip()
            txt = re.sub(r'^```(?:json)?\s*|\s*```$', '', txt)
            mt = re.search(r'\{.*\}', txt, re.S)
            try:
                _LOCAL['last'] = json.loads(mt.group(0) if mt else txt)
            except ValueError:
                _LOCAL['last'] = dict(raw=txt[:400])
            return _LOCAL['last'] if 'raw' not in _LOCAL['last'] else None
    return LocalLLMParser


def lenient_window(j):
    """What the LLM read, scored even when the library could not build a Maintenance (missing mandatory fields): start/end
    taken from the JSON by the library's own key lookup and datetime conversion (isoparse, naive -> local zone, run with TZ=UTC)."""
    if not isinstance(j, dict):
        return dict(start_utc=None, end_utc=None)
    from circuit_maintenance_parser.parser import LLM
    out = {}
    for k in ('start', 'end'):
        key = LLM.get_key_with_string(j, k); v = j.get(key) if key else None
        try:
            if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r'\d{9,11}', v.strip())):
                epoch = int(float(v))                      # the model answered with an epoch; the library's isoparse would reject it
            else:
                epoch = LLM()._convert_str_datetime_to_epoch(str(v)) if v else None
            out[k + '_utc'] = D.datetime.fromtimestamp(epoch, D.timezone.utc).strftime('%Y-%m-%dT%H:%M') if epoch else None
        except Exception:
            out[k + '_utc'] = None
    return out


def cmp_parse(text, title, posted, llm, local_model=None):
    """-> dict(provider, status, start_utc, end_utc, error). llm=True: the library's LLM fallback path (GenericProvider,
    add_subject_to_text, CombinedProcessor[EmailDateParser, LLM parser]); with local_model the LLM parser is served locally,
    otherwise by OpenAI (PARSER_OPENAI_API_KEY, PARSER_OPENAI_MODEL). llm=False: the registered provider parsers only."""
    from circuit_maintenance_parser import provider as P
    if llm and local_model:
        from circuit_maintenance_parser.parser import EmailDateParser
        from circuit_maintenance_parser.processor import CombinedProcessor
        os.environ.pop('PARSER_OPENAI_API_KEY', None)
        prov = P.GenericProvider(); nd = _notification(text, title, posted); prov.add_subject_to_text(nd); _LOCAL['last'] = None
        try:
            ms = list(CombinedProcessor(data_parsers=[EmailDateParser, local_llm_parser(local_model)]).process(nd, prov.get_extended_data()))
        except Exception as exc:
            ms = []; err = f'{type(exc).__name__}: {str(exc)[:200]}'
        else:
            err = None if ms else 'no maintenance'
        j = _LOCAL.get('last'); lw = lenient_window(j)
        if not ms:
            return dict(provider='GenericProvider+local', status=(j or {}).get('status') if isinstance(j, dict) else None, start_utc=None, end_utc=None, error=err, llm_json=j, lenient=lw)
        mt = ms[0]; st = getattr(mt, 'status', None); st = st.value if hasattr(st, 'value') else str(st)
        f = lambda t: D.datetime.fromtimestamp(int(t), D.timezone.utc).strftime('%Y-%m-%dT%H:%M') if t else None
        return dict(provider='GenericProvider+local', status=st, start_utc=f(mt.start), end_utc=f(mt.end), n=len(ms), llm_json=j, lenient=lw)
    if llm:
        assert os.environ.get('PARSER_OPENAI_API_KEY'), 'PARSER_OPENAI_API_KEY is required for cmp_llm'
        provs = [('GenericProvider', P.GenericProvider)]
        from circuit_maintenance_parser.parsers import openai as _oa
        if not getattr(_oa.OpenAIParser, '_captured', False):          # keep the JSON the library received, for the lenient scoring
            _orig = _oa.OpenAIParser.get_llm_response
            def _capture(self, content):
                r = _orig(self, content); _LOCAL['last'] = r; _LOCAL['calls'] = _LOCAL.get('calls', 0) + 1; return r
            _oa.OpenAIParser.get_llm_response = _capture; _oa.OpenAIParser._captured = True
        _LOCAL['last'] = None
    else:
        os.environ.pop('PARSER_OPENAI_API_KEY', None); provs = _providers()
    errors = []
    for name, cls in provs:
        try:
            ms = list(cls().get_maintenances(_notification(text, title, posted)))
        except Exception as exc:
            errors.append(f'{name}: {type(exc).__name__}: {str(exc)[:160]}'); continue
        if ms:
            mt = ms[0]; st = getattr(mt, 'status', None); st = st.value if hasattr(st, 'value') else str(st)
            f = lambda t: D.datetime.fromtimestamp(int(t), D.timezone.utc).strftime('%Y-%m-%dT%H:%M') if t else None
            out = dict(provider=name, status=st, start_utc=f(mt.start), end_utc=f(mt.end), n=len(ms))
            if llm:
                out.update(llm_json=_LOCAL.get('last'), lenient=lenient_window(_LOCAL.get('last')))
            return out
    out = dict(provider=None, status=None, start_utc=None, end_utc=None, error='; '.join(errors)[:300])
    if llm:
        j = _LOCAL.get('last'); out.update(llm_json=j, lenient=lenient_window(j), status=(j or {}).get('status') if isinstance(j, dict) else None)
    return out


def cmp_action(parsed, it):
    st = (parsed.get('status') or '').upper()
    if st == 'CANCELLED':
        return 'cancelled'
    if st == 'RE-SCHEDULED':
        return 'moved'
    s, e = mce.parse_minute(parsed.get('start_utc')), mce.parse_minute(parsed.get('end_utc'))
    a_s = mce.parse_minute(SB.MR.extract(it['announcement'], it['announced_utc'])['start_utc'])   # announced start by the development rule (as rule_pred)
    if s is not None and (a_s is None or abs((s - a_s).total_seconds()) > mce.TOL_MIN * 60):
        return 'moved'
    if e is not None and s is not None and a_s is not None:
        return 'extended'
    return 'unchanged'


def run_time(a):
    if a.target == 'ann':
        items = [i for i in mce.items(m.ROOT / 'data/maintenance_notices_20260929') if i['part'] == a.part]
        text_of, posted_of, title_of = (lambda i: i['text']), (lambda i: i['posted_utc']), (lambda i: i.get('title'))
    else:
        items = [dict(i, truth=RCE.ACTION[i['kind']]) for i in RCE.items() if i['part'] == a.part]
        text_of, posted_of, title_of = (lambda i: i['update']), (lambda i: i['update_utc']), (lambda i: i.get('title'))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.tool == 'heideltime':
        for glue in (False, True):
            rows = []
            for it in items:
                try:
                    ex = expressions_heideltime(text_of(it), posted_of(it)); w = window_from(ex, text_of(it), posted_of(it), glue)
                except Exception as exc:
                    ex, w = [], dict(start_utc=None, end_utc=None, error=repr(exc)[:120])
                exs = [dict(text=e['text'], clock=e['clock'], date=e['date']) for e in ex]
                if a.target == 'ann':
                    rows.append(dict(it, pred=w, expressions=exs, score=mce.score(w, it)))
                else:
                    act = RCE.ACTION.get(RCE.anu.kind_of(it['update']) or '', 'unchanged'); pred = dict(action=act, **w)
                    rows.append(dict(it, pred=pred, expressions=exs, score=RCE.score(pred, it)))
            _write(out, a, f"heideltime_{'glue' if glue else 'raw'}", rows)
        return
    llm = a.tool in ('cmp_llm', 'cmp_llm_local'); local = a.local_model if a.tool == 'cmp_llm_local' else None
    label = a.tool + (('_' + local) if local else ('_' + os.environ.get('PARSER_OPENAI_MODEL', 'gpt-3.5-turbo').replace('.', 'p')) if llm else '')
    done = {}
    f = out / f'{a.target}_{a.part}_{label}.jsonl'
    if f.exists():   # resume: one LLM call per item, never repeated
        done = {json.loads(l)['id']: json.loads(l) for l in f.read_text().splitlines() if l.strip()}
    rows = []
    for it in items:
        if it['id'] in done:
            rows.append(done[it['id']]); continue
        if a.target == 'ann':
            parsed = cmp_parse(text_of(it), title_of(it), posted_of(it), llm, local); w = dict(start_utc=parsed['start_utc'], end_utc=parsed['end_utc'])
            row = dict(it, pred=w, parsed=parsed, score=mce.score(w, it))
            if parsed.get('lenient'):
                row['pred_lenient'] = parsed['lenient']; row['score_lenient'] = mce.score(parsed['lenient'], it)
            rows.append(row)
        else:
            text = f"Original announcement (posted {it['announced_utc']} UTC):\n{it['announcement']}\n\nUpdate (posted {it['update_utc']} UTC):\n{it['update']}"
            parsed = cmp_parse(text, title_of(it), posted_of(it), llm, local)
            pred = dict(action=cmp_action(parsed, it), start_utc=parsed['start_utc'], end_utc=parsed['end_utc'])
            row = dict(it, pred=pred, parsed=parsed, score=RCE.score(pred, it))
            if parsed.get('lenient'):
                lp = dict(parsed, start_utc=parsed['lenient']['start_utc'], end_utc=parsed['lenient']['end_utc'])
                row['pred_lenient'] = dict(action=cmp_action(lp, it), **parsed['lenient']); row['score_lenient'] = RCE.score(row['pred_lenient'], it)
            rows.append(row)
        if llm:
            f.write_text(''.join(json.dumps(r, ensure_ascii=False, default=str) + '\n' for r in rows))
    _write(out, a, label, rows)
    if llm:
        print('model calls this run', _LOCAL.get('calls', 0), flush=True)
        if any('score_lenient' in r for r in rows):
            _write(out, a, label + '_lenient', [dict(r, pred=r['pred_lenient'], score=r['score_lenient']) for r in rows if 'score_lenient' in r])


def _write(out, a, label, rows):
    (out / f'{a.target}_{a.part}_{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False, default=str) + '\n' for r in rows))
    summ = mce.summarise(rows) if a.target == 'ann' else RCE.summarise(rows); print(a.target, a.part, label, json.dumps(summ)[:400], flush=True)


def main():
    p = argparse.ArgumentParser(); p.add_argument('cmd', choices=['time']); p.add_argument('--tool', required=True, choices=['heideltime', 'cmp_rules', 'cmp_llm', 'cmp_llm_local']); p.add_argument('--local-model', default='qwen7b', help='asset name in .runtime/ubuntu-pro6000/assets.json for cmp_llm_local')
    p.add_argument('--part', default='dev'); p.add_argument('--target', choices=['ann', 'upd'], default='ann'); p.add_argument('--out', required=True); a = p.parse_args()
    run_time(a)


if __name__ == '__main__':
    main()
