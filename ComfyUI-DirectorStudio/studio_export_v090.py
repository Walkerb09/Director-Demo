"""Export the Director's edit for other editors, with the original takes alongside so it relinks.

  <slug>_edit/
    media/<take>.mp4                 the exact takes used (hash-checked)
    <slug>.edl                       CMX 3600 EDL, 24 fps non-drop: DaVinci Resolve, Premiere, Avid, most NLEs
    <slug>_premiere_resolve.xml      Final Cut Pro 7 XML (xmeml v4): Premiere Pro and DaVinci Resolve
    <slug>_finalcut.fcpxml           FCPXML 1.9: Final Cut Pro and DaVinci Resolve
    README.txt                       how to import
Picture cuts are exact; J/L cuts are written as separate audio edits. Colour matching and fades are not
exported (do them in your editor).
"""
from __future__ import annotations
import hashlib, json, os, shutil, zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

FPS = 24
REC_START = 3600 * FPS            # 01:00:00:00

def tc(frames):
    f = frames % FPS; s = frames // FPS
    return f'{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}'

def _clips(tl):
    """Timeline clips -> events with record positions; audio spans may differ from picture (J/L)."""
    ev = []; rec = 0
    for i, c in enumerate(tl):
        n = c['out'] - c['in']
        aa, ab = c.get('a_in', c['in']), c.get('a_out', c['out'])
        ev.append({'n': i + 1, 'file': c['file'], 'shot': c['shot'], 'take': c['take'], 'v_in': c['in'], 'v_out': c['out'], 'rec_in': rec,
                   'rec_out': rec + n, 'a_in': aa, 'a_out': ab, 'a_rec_in': rec + (aa - c['in']), 'a_rec_out': rec + (aa - c['in']) + (ab - aa),
                   'frames': c.get('frames') or 124})
        rec += n
    a_total = sum(e['a_out'] - e['a_in'] for e in ev)
    if a_total != rec: raise ValueError('AUDIO_AND_PICTURE_LENGTHS_DIFFER')
    return ev, rec

def edl(title, tl):
    ev, _ = _clips(tl)
    out = [f'TITLE: {title[:60]}', 'FCM: NON-DROP FRAME', '']
    k = 1
    for e in ev:
        reel = f'S{e["shot"]:02d}T{e["take"]:02d}'
        out.append(f'{k:03d}  {reel:<8} V     C        {tc(e["v_in"])} {tc(e["v_out"])} {tc(REC_START + e["rec_in"])} {tc(REC_START + e["rec_out"])}')
        out.append(f'* FROM CLIP NAME: {e["file"]}'); out.append(''); k += 1
        out.append(f'{k:03d}  {reel:<8} A     C        {tc(e["a_in"])} {tc(e["a_out"])} {tc(REC_START + e["a_rec_in"])} {tc(REC_START + e["a_rec_out"])}')
        out.append(f'* FROM CLIP NAME: {e["file"]}'); out.append(''); k += 1
    return '\n'.join(out) + '\n'

def xmeml(title, tl, width=768, height=1344):
    ev, total = _clips(tl)
    files = {}
    def fileref(e):
        if e['file'] in files: return f'<file id={quoteattr(files[e["file"]])}/>'
        fid = f'file-{len(files) + 1}'; files[e['file']] = fid
        return (f'<file id={quoteattr(fid)}><name>{escape(e["file"])}</name><pathurl>{escape("media/" + e["file"])}</pathurl>'
                f'<rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate><duration>{e["frames"]}</duration>'
                f'<media><video><samplecharacteristics><width>{width}</width><height>{height}</height></samplecharacteristics></video>'
                f'<audio><channelcount>2</channelcount></audio></media></file>')
    rate = f'<rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>'
    v = []; a = []
    for e in ev:
        f = fileref(e)
        v.append(f'<clipitem id="v{e["n"]}"><name>{escape(e["file"])}</name>{rate}<start>{e["rec_in"]}</start><end>{e["rec_out"]}</end>'
                 f'<in>{e["v_in"]}</in><out>{e["v_out"]}</out>{f}</clipitem>')
        a.append(f'<clipitem id="a{e["n"]}"><name>{escape(e["file"])}</name>{rate}<start>{e["a_rec_in"]}</start><end>{e["a_rec_out"]}</end>'
                 f'<in>{e["a_in"]}</in><out>{e["a_out"]}</out><file id={quoteattr(files[e["file"]])}/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1">'
            f'<name>{escape(title)}</name><duration>{total}</duration>{rate}<timecode>{rate}<string>01:00:00:00</string><frame>{REC_START}</frame></timecode>'
            f'<media><video><format><samplecharacteristics><width>{width}</width><height>{height}</height>{rate}</samplecharacteristics></format>'
            f'<track>{"".join(v)}</track></video><audio><track>{"".join(a)}</track></audio></media></sequence></xmeml>\n')

def fcpxml(title, tl, width=768, height=1344):
    ev, total = _clips(tl)
    def t(fr): return f'{fr}/{FPS}s'
    assets = {}; res = [f'<format id="r0" name="FFVideoFormat{height}p24" frameDuration="1/{FPS}s" width="{width}" height="{height}"/>']
    for e in ev:
        if e['file'] in assets: continue
        aid = f'r{len(assets) + 1}'; assets[e['file']] = aid
        res.append(f'<asset id="{aid}" name={quoteattr(e["file"])} start="0s" duration="{t(e["frames"])}" hasVideo="1" hasAudio="1" format="r0" '
                   f'audioSources="1" audioChannels="2"><media-rep kind="original-media" src={quoteattr("media/" + e["file"])}/></asset>')
    spine = []
    for e in ev:
        lead = e['a_in'] - e['v_in']; dur_a = e['a_out'] - e['a_in']
        # picture on the spine without its audio; the (possibly J/L-shifted) audio as a connected clip
        spine.append(f'<asset-clip ref="{assets[e["file"]]}" name={quoteattr(e["file"])} offset="{t(e["rec_in"])}" start="{t(e["v_in"])}" '
                     f'duration="{t(e["v_out"] - e["v_in"])}" audioRole="dialogue" srcEnable="video">'
                     f'<asset-clip ref="{assets[e["file"]]}" lane="-1" offset="{t(e["v_in"] + lead)}" start="{t(e["a_in"])}" duration="{t(dur_a)}" '
                     f'srcEnable="audio" audioRole="dialogue"/></asset-clip>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9"><resources>' + ''.join(res) + '</resources>'
            f'<library><event name={quoteattr(title)}><project name={quoteattr(title)}><sequence format="r0" duration="{t(total)}" tcStart="0s" tcFormat="NDF">'
            f'<spine>{"".join(spine)}</spine></sequence></project></event></library></fcpxml>\n')

README = """Director Studio edit export
===========================
media/                 the original takes (do not rename them)
{slug}.edl             EDL: DaVinci Resolve (File > Import > Timeline), Premiere (File > Import), Avid
{slug}_premiere_resolve.xml   Premiere Pro (File > Import) / DaVinci Resolve (File > Import > Timeline)
{slug}_finalcut.fcpxml        Final Cut Pro (File > Import > XML) / DaVinci Resolve
Unzip the whole folder first. If your editor asks for media, point it at the media/ folder.
Timeline: 24 fps, {w}x{h}, starts at 01:00:00:00. Picture cuts are frame-exact; J/L cuts are separate audio
edits. The Director's colour match and 12 ms audio fades are not included (do them in your editor).
Shots: {shots}
"""

def export_package(studio, dest_zip):
    tl = studio.timeline()
    if not tl: raise ValueError('NOTHING_TO_EXPORT_YET')
    import studio_v080 as _sv
    tl = _sv.expand_pieces(tl)                         # ✂ V0.9.68 one edit per piece you kept
    st = studio.state(); slug = st['project']['slug']; title = st['project']['title']
    fmt = st['project']['format']; w, h = fmt.get('width', 768), fmt.get('height', 1344)
    for c in tl:
        t = next(t for t in st['shots'][c['shot']]['takes'] if t['take'] == c['take'])
        c['frames'] = t.get('frames') or 124
        f = studio.take_path(t)
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != c['sha256']: raise ValueError('TAKE_MISSING_OR_CHANGED ' + c['file'])
    dest_zip = Path(dest_zip); tmp = dest_zip.with_name(dest_zip.name + '.partial')
    base = f'{slug}_edit/'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(base + f'{slug}.edl', edl(title, tl))
        z.writestr(base + f'{slug}_premiere_resolve.xml', xmeml(title, tl, w, h))
        z.writestr(base + f'{slug}_finalcut.fcpxml', fcpxml(title, tl, w, h))
        z.writestr(base + 'README.txt', README.format(slug=slug, w=w, h=h, shots=', '.join(f"S{c['shot']}T{c['take']} F{c['in']}-{c['out'] - 1}" for c in tl)))
        for c in tl:
            t = next(t for t in st['shots'][c['shot']]['takes'] if t['take'] == c['take'])
            if base + 'media/' + c['file'] not in z.namelist(): z.write(studio.take_path(t), base + 'media/' + c['file'], compress_type=zipfile.ZIP_STORED)
    os.replace(tmp, dest_zip)
    return {'path': str(dest_zip), 'clips': len(tl), 'frames': sum(c['out'] - c['in'] for c in tl)}
