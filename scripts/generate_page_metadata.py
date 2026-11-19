#!/usr/bin/env python3
"""Generate and validate canonical DIO/ODI page metadata for this static site."""
from __future__ import annotations
import argparse, hashlib, html, json, re, sys, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REPOSITORY="dan-beranek/mapping-digital-optimizations-in-ai-mediated-environments"
REGISTRY_URL="https://raw.githubusercontent.com/dan-beranek/dio-odi-reference-model/main/metadata/keywords.json"
SNAPSHOT=ROOT/"metadata/keyword-registry.snapshot.json"
CHUNK_ORDER=["C0_identity_invariant","C1_reconstruction_environments","C2_work_positioning","C3_reconstruction_mechanics","C4_k6","C5_failures_robustness","C6_machine_readable_representation"]
MARKER='data-dio-odi-generated="page-keyword-metadata"'
def load_registry():
    try:
        req=urllib.request.Request(REGISTRY_URL,headers={"User-Agent":"DIO-ODI-page-metadata-builder/1.0"})
        with urllib.request.urlopen(req,timeout=12) as response: raw=response.read()
        source=REGISTRY_URL
    except Exception as exc:
        if not SNAPSHOT.is_file(): raise RuntimeError(f"Registry unavailable and snapshot missing: {exc}") from exc
        raw=SNAPSHOT.read_bytes(); source="committed-snapshot"
    registry=json.loads(raw.decode("utf-8"))
    if registry.get("status")!="canonical-approved" or not re.fullmatch(r"2\.\d+\.\d+",registry.get("registry_version","")): raise ValueError("Unsupported or unapproved canonical registry")
    return raw,registry,source
def resolve(registry,pid,profile):
    if profile.get("status")!="canonical-approved" or profile.get("scope")!="derived-public-webpage": raise ValueError(f"{pid}: profile not approved")
    sel=profile.get("chunks",{}); chunks=registry.get("chunks",{})
    if sel.get("C0_identity_invariant")!="all" or not isinstance(sel.get("C1_reconstruction_environments"),list): raise ValueError(f"{pid}: invalid C0/C1")
    result=[]
    for name in CHUNK_ORDER:
        if name not in sel or name not in chunks: raise ValueError(f"{pid}: missing {name}")
        source=chunks[name].get("terms",[]); choice=sel[name]
        if choice=="all": selected=list(source)
        elif choice=="none": selected=[]
        elif isinstance(choice,list):
            selected=choice
            if name!="C2_work_positioning":
                ix=[source.index(x) if x in source else -1 for x in selected]
                if -1 in ix or ix!=sorted(ix) or len(set(selected))!=len(selected): raise ValueError(f"{pid}: invalid {name} selection")
            elif len(set(selected))!=len(selected): raise ValueError(f"{pid}: duplicate C2")
        else: raise ValueError(f"{pid}: invalid {name} selector")
        result.extend(selected)
    if len({x.casefold() for x in result})!=len(result): raise ValueError(f"{pid}: duplicate terms")
    required=set(result)-set(chunks["C0_identity_invariant"]["terms"])
    rationales=profile.get("term_rationales",{})
    if set(rationales)!=required: raise ValueError(f"{pid}: rationale mismatch; missing={sorted(required-set(rationales))}; extra={sorted(set(rationales)-required)}")
    return result
def inject_jsonld(content,pid,profile,terms):
    pat=re.compile(r'(<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>)([\s\S]*?)(</script>)',re.I); changed=0
    def replace(m):
        nonlocal changed
        data=json.loads(m.group(2)); nodes=[]
        def walk(v):
            if isinstance(v,list):
                for x in v: walk(x)
            elif isinstance(v,dict):
                nodes.append(v)
                for x in v.values(): walk(x)
        walk(data); s=profile["surface"]
        if s.get("entity_id"): matches=[n for n in nodes if n.get("@id")==s["entity_id"]]
        else:
            matches=[]
            for n in nodes:
                kinds=n.get("@type",[])
                if isinstance(kinds,str): kinds=[kinds]
                if s.get("entity_type") in kinds and n.get("url")==s.get("entity_url"): matches.append(n)
        if len(matches)!=1: raise ValueError(f"{pid}: expected exactly one JSON-LD target, got {len(matches)}")
        matches[0]["keywords"]=terms; changed+=1
        return m.group(1)+"\n"+json.dumps(data,ensure_ascii=False,indent=2)+"\n"+m.group(3)
    result=pat.sub(replace,content)
    if not changed: raise ValueError(f"{pid}: no JSON-LD found")
    return result
def update_page(path,pid,profile,terms,version):
    file=ROOT/path
    if not file.is_file(): raise FileNotFoundError(f"{pid}: {path}")
    content=file.read_text(encoding="utf-8")
    content=re.sub(r'<(?:meta|link)\b(?=[^>]*'+re.escape(MARKER)+r')[^>]*>\s*',"",content,flags=re.I)
    content=re.sub(r'<meta\b(?=[^>]*\bname=["\']keywords["\'])[^>]*>\s*',"",content,flags=re.I)
    content=inject_jsonld(content,pid,profile,terms)
    tags=[
      f'<meta name="keywords" content="{html.escape(", ".join(terms),quote=True)}" {MARKER}>',
      f'<meta name="dio-odi-keyword-profile-id" content="{html.escape(pid,quote=True)}" {MARKER}>',
      f'<meta name="dio-odi-keyword-registry-version" content="{html.escape(version,quote=True)}" {MARKER}>',
      f'<link rel="describedby" href="assets/data/resolved-page-metadata.json" {MARKER}>'
    ]
    if not re.search(r'</head>',content,re.I): raise ValueError(f"{pid}: </head> missing")
    return re.sub(r'</head>',"\n".join(tags)+"\n</head>",content,count=1,flags=re.I)
def build():
    raw,registry,source=load_registry(); sha=hashlib.sha256(raw).hexdigest()
    outputs={"metadata/keyword-registry.snapshot.json":raw.decode("utf-8")}; artifacts=[]
    for pid,profile in sorted(registry.get("surface_profiles",{}).items()):
        s=profile.get("surface",{})
        if s.get("repository")!=REPOSITORY: continue
        paths,urls=s.get("paths"),s.get("urls")
        if not isinstance(paths,list) or len(paths)!=1 or not isinstance(urls,list) or len(urls)!=1: raise ValueError(f"{pid}: exactly one path/URL required")
        terms=resolve(registry,pid,profile); outputs[paths[0]]=update_page(paths[0],pid,profile,terms,registry["registry_version"])
        artifacts.append({"profile_id":pid,"status":profile["status"],"scope":profile["scope"],"purpose":profile["purpose"],
          "page":{"page_key":s.get("page_key"),"path":paths[0],"url":urls[0],"entity_id":s.get("entity_id"),"entity_type":s.get("entity_type"),"entity_url":s.get("entity_url")},
          "chunk_selectors":profile["chunks"],"resolved_keywords":terms,"term_rationales":profile["term_rationales"]})
    if not artifacts: raise ValueError(f"No profiles for {REPOSITORY}")
    manifest={"schema_version":"1.0.0","site":{"repository":REPOSITORY,"origin":artifacts[0]["page"]["url"].rsplit("/",1)[0]+"/"},
      "keyword_registry":{"repository":"dan-beranek/dio-odi-reference-model","path":"metadata/keywords.json","registry_version":registry["registry_version"],"status":registry["status"],"sha256":sha,"source":source,"source_url":REGISTRY_URL,"snapshot_path":"metadata/keyword-registry.snapshot.json"},"artifacts":artifacts}
    outputs["assets/data/resolved-page-metadata.json"]=json.dumps(manifest,ensure_ascii=False,indent=2)+"\n"
    return outputs
def main():
    parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(); group.add_argument("--check",action="store_true"); group.add_argument("--write",action="store_true"); args=parser.parse_args()
    try:
        outputs=build(); stale=[]
        for rel,want in outputs.items():
            path=ROOT/rel; current=path.read_text(encoding="utf-8") if path.is_file() else None
            if current!=want:
                stale.append(rel)
                if not args.check: path.parent.mkdir(parents=True,exist_ok=True); path.write_text(want,encoding="utf-8",newline="\n")
        if args.check and stale: print("Stale generated metadata:\n"+"\n".join("- "+x for x in stale),file=sys.stderr); return 1
        print("Page metadata is reproducible." if args.check else f"Generated {len(outputs)-2} page(s)."); return 0
    except Exception as exc: print(f"PAGE METADATA BUILD FAILED: {exc}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
