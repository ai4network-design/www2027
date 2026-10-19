"""Request-independent finite-state syntax: structure only, never optimal values."""
import hashlib
import json


class Builder:
    def __init__(self): self.edges=[];self.eps=[]
    def node(self):
        self.edges.append({});self.eps.append(set());return len(self.edges)-1
    def edge(self,a,char,b): self.edges[a].setdefault(char,set()).add(b)
    def literal(self,text):
        first=self.node();last=first
        for c in text:
            nxt=self.node();self.edge(last,c,nxt);last=nxt
        return first,last
    def seq(self,*parts):
        for a,b in zip(parts,parts[1:]):self.eps[a[1]].add(b[0])
        return parts[0][0],parts[-1][1]
    def either(self,*parts):
        first,last=self.node(),self.node()
        for a,b in parts:self.eps[first].add(a);self.eps[b].add(last)
        return first,last
    def number(self):
        first,zero,integer,dot,fraction,last=[self.node() for _ in range(6)]
        self.edge(first,'0',zero)
        for c in '123456789':self.edge(first,c,integer)
        for c in '0123456789':
            self.edge(integer,c,integer);self.edge(dot,c,fraction);self.edge(fraction,c,fraction)
        for n in (zero,integer):self.edge(n,'.',dot)
        for n in (zero,integer,fraction):self.eps[n].add(last)
        return first,last
    def closure(self,nodes):
        found=set(nodes);todo=list(nodes)
        while todo:
            for nxt in self.eps[todo.pop()]-found:found.add(nxt);todo.append(nxt)
        return frozenset(found)
    def dfa(self,part):
        states=[self.closure([part[0]])];lookup={states[0]:0};transitions=[];accepting=[]
        for state in states:
            choices={}
            for n in state:
                for c,targets in self.edges[n].items():choices.setdefault(c,set()).update(targets)
            row={}
            for c,targets in sorted(choices.items()):
                nxt=self.closure(targets)
                if nxt not in lookup:lookup[nxt]=len(states);states.append(nxt)
                row[c]=lookup[nxt]
            transitions.append(row);accepting.append(part[1] in state)
        return dict(start=0,transitions=transitions,accepting=accepting)


def grammar(phase):
    b=Builder();lit=b.literal;num=b.number;seq=b.seq;alt=b.either
    if phase=='intent':
        part=seq(lit('[['),num(),lit(','),num(),lit(','),num(),lit('],['),num(),lit(','),num(),lit(','),num(),lit(']]'))
    elif phase=='action':
        names=alt(*(lit(json.dumps(n)) for n in ('query','video','backup')))
        op=alt(seq(lit(',"path":'),alt(*(lit(str(i)) for i in (1,2,3)))),
               seq(lit(',"rate_mbps":'),alt(lit('null'),num())))
        part=alt(lit('{"hold":true}'),seq(lit('{"service":'),names,op,lit('}')))
    else:raise ValueError('phase')
    return b.dfa(part)


def advance(dfa,state,text):
    for char in text:
        state=dfa['transitions'][state].get(char)
        if state is None:return None
    return state


def complete(dfa,text):
    state=advance(dfa,dfa['start'],text)
    return state is not None and dfa['accepting'][state]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def compile_tables(tokenizer,output_vocab,denied_ids,grammar_factory=grammar):
    denied=set(denied_ids);size=len(tokenizer)
    tables={p:grammar_factory(p) for p in ('intent','action')}
    alphabet=set().union(*(set(row) for dfa in tables.values() for row in dfa['transitions']))
    text={}
    for token in range(min(size,output_vocab)):
        if token in denied:continue
        value=tokenizer.decode([token],skip_special_tokens=False,clean_up_tokenization_spaces=False)
        if value and all(c in alphabet for c in value):text[str(token)]=value
    for dfa in tables.values():
        rows=[]
        for state in range(len(dfa['transitions'])):
            allowed=[int(token) for token,value in text.items() if advance(dfa,state,value) is not None]
            if not allowed and not dfa['accepting'][state]:raise ValueError('tokenizer cannot express grammar prefix')
            rows.append(allowed)
        dfa['allowed_ids']=rows
    used=set().union(*(set(ids) for d in tables.values() for ids in d['allowed_ids']))
    result=dict(version='syntax-dfa-v1',output_vocab=output_vocab,
                token_text={k:v for k,v in text.items() if int(k) in used},tables=tables)
    result['sha256']=digest(result)
    return result


class Processor:
    def __init__(self,torch,tokenizer,compiled,device):
        self.torch=torch;self.tokenizer=tokenizer;self.compiled=compiled
        self.indices={phase:[torch.tensor(row,dtype=torch.long,device=device) for row in d['allowed_ids']]
                      for phase,d in compiled['tables'].items()}
        self.phase=None;self.input_size=None
    def begin(self,phase,input_size):self.phase=phase;self.input_size=input_size
    def __call__(self,input_ids,scores):
        raw=self.tokenizer.decode(input_ids[0,self.input_size:].tolist(),skip_special_tokens=False,clean_up_tokenization_spaces=False)
        dfa=self.compiled['tables'][self.phase];state=advance(dfa,0,raw)
        if state is None or dfa['accepting'][state]:raise ValueError('invalid prefix or generation continued after structural completion')
        ids=self.indices[self.phase][state]
        result=self.torch.full_like(scores,float('-inf'))
        return result.index_copy(-1,ids,scores.index_select(-1,ids))
