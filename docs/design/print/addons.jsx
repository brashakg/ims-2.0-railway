/* global React */
/* Reusable visual add-ons for print templates:
   - Header variants (3)
   - Footer variants (3)
   - Lens / eye / frame / PD diagrams (SVG, grayscale, documentary style)
   - Mini sparklines and stock bars
   - Dos & Don'ts annotation overlay (teaching mode)
*/

/* ═══════════════════════ Header variants ═══════════════════════ */
const BV_INFO = {
  addr: 'Shop 14, M-Block Market, Greater Kailash-I, New Delhi 110048',
  phone: '+91 11 4135 2010', email: 'gk1@bettervision.in',
  gstin: '07AABCB1234M1Z5', cin: 'U33200DL2018PTC332100', drug: 'DL-OPT-GK1-2018-0421',
};

function Header({ variant='band', docType, docNumber, meta=[], accent='#C2410C' }) {
  if (variant === 'minimal') {
    return (
      <div style={{padding:'24px 44px 14px', borderBottom:'1px solid var(--ink)', display:'grid', gridTemplateColumns:'1fr auto', gap:24, alignItems:'baseline'}}>
        <div>
          <div style={{fontFamily:'var(--font-display)', fontSize:22, letterSpacing:'-.01em', lineHeight:1}}>Better Vision</div>
          <div style={{fontFamily:'var(--font-mono)', fontSize:9, color:'var(--ink-4)', textTransform:'uppercase', letterSpacing:'.14em', marginTop:4}}>
            Opticals · Since 1987 · GSTIN {BV_INFO.gstin}
          </div>
        </div>
        <div style={{textAlign:'right'}}>
          <div style={{fontFamily:'var(--font-mono)', fontSize:9, color:'var(--ink-4)', textTransform:'uppercase', letterSpacing:'.14em'}}>{docType}</div>
          <div style={{fontFamily:'var(--font-figure)', fontWeight:600, fontSize:18, letterSpacing:'-.01em', fontVariantNumeric:'tabular-nums', marginTop:2}}>{docNumber}</div>
        </div>
      </div>
    );
  }

  if (variant === 'tall') {
    return (
      <div style={{padding:'32px 44px 18px', borderBottom:'2px solid var(--ink)', display:'grid', gridTemplateColumns:'auto 1fr auto', gap:22, alignItems:'center'}}>
        <div style={{width:70, height:70, borderRadius:4, background: accent, color:'#fff', display:'grid', placeItems:'center', fontFamily:'var(--font-display)', fontSize:44, lineHeight:1}}>B</div>
        <div>
          <div style={{fontFamily:'var(--font-display)', fontSize:30, letterSpacing:'-.01em', lineHeight:1}}>Better Vision Opticals</div>
          <div style={{fontFamily:'var(--font-mono)', fontSize:9.5, color:'var(--ink-4)', textTransform:'uppercase', letterSpacing:'.14em', marginTop:3}}>Est. 1987 · Delhi · Mumbai · Bengaluru · Pune</div>
          <div style={{fontSize:10, color:'var(--ink-3)', marginTop:6, lineHeight:1.55}}>{BV_INFO.addr}<br/>{BV_INFO.phone} · {BV_INFO.email}</div>
        </div>
        <div style={{textAlign:'right', borderLeft:'1px solid var(--line)', paddingLeft:22}}>
          <div style={{fontFamily:'var(--font-mono)', fontSize:9, color:'var(--ink-4)', textTransform:'uppercase', letterSpacing:'.14em'}}>{docType}</div>
          <div style={{fontFamily:'var(--font-figure)', fontWeight:600, fontSize:22, letterSpacing:'-.02em', fontVariantNumeric:'tabular-nums', margin:'4px 0 8px'}}>{docNumber}</div>
          {meta.map(([k,v]) => (
            <div key={k} style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'2px 12px', fontSize:10, textAlign:'left', justifyContent:'end'}}>
              <span style={{color:'var(--ink-4)'}}>{k}</span>
              <span className="mono">{v}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // band (default, current design)
  return (
    <div style={{display:'grid', gridTemplateColumns:'1fr auto', gap:24, alignItems:'start', padding:'36px 44px 18px', borderBottom:'2px solid var(--ink)'}}>
      <div>
        <div style={{display:'flex', alignItems:'center', gap:14}}>
          <div style={{width:52, height:52, borderRadius:10, background: accent, color:'#fff', display:'grid', placeItems:'center', fontFamily:'var(--font-display)', fontSize:30}}>B</div>
          <div>
            <div style={{fontFamily:'var(--font-display)', fontSize:30, lineHeight:1, letterSpacing:'-.01em'}}>Better Vision</div>
            <div style={{fontSize:10.5, color:'var(--ink-3)', fontFamily:'var(--font-mono)', textTransform:'uppercase', letterSpacing:'.12em', marginTop:3}}>Opticals · Since 1987</div>
          </div>
        </div>
        <div style={{fontSize:10.5, color:'var(--ink-3)', marginTop:10, lineHeight:1.6, maxWidth:420}}>
          {BV_INFO.addr}<br/>
          {BV_INFO.phone} · {BV_INFO.email}<br/>
          <span className="mono">GSTIN {BV_INFO.gstin} · CIN {BV_INFO.cin} · Drug Lic. {BV_INFO.drug}</span>
        </div>
      </div>
      <div style={{textAlign:'right'}}>
        <div style={{fontFamily:'var(--font-mono)', fontSize:10, textTransform:'uppercase', letterSpacing:'.14em', color:'var(--ink-4)', marginBottom:8}}>{docType}</div>
        <div style={{fontFamily:'var(--font-figure)', fontWeight:600, fontSize:22, letterSpacing:'-.02em', fontVariantNumeric:'tabular-nums'}}>{docNumber}</div>
        {meta.length > 0 && (
          <div style={{marginTop:10, display:'grid', gridTemplateColumns:'auto 1fr', gap:'4px 12px', fontSize:10.5, textAlign:'left', justifyContent:'end'}}>
            {meta.map(([k,v]) => <React.Fragment key={k}><span style={{color:'var(--ink-4)'}}>{k}</span><span className="mono">{v}</span></React.Fragment>)}
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════ Footer variants ═══════════════════════ */
function Footer({ variant='legal', docUrl, accent='#C2410C' }) {
  if (variant === 'qr') {
    return (
      <div style={{padding:'14px 44px', borderTop:'1px solid var(--line)', background:'var(--bg-sunk)', display:'grid', gridTemplateColumns:'auto 1fr auto', gap:20, alignItems:'center'}}>
        <MiniQR label="Verify" />
        <div style={{fontSize:10.5, color:'var(--ink-3)', lineHeight:1.55}}>
          <b style={{color:'var(--ink)'}}>Terms:</b> Lens orders are final once edging starts. Frames exchangeable within 7 days if unused and unscratched. Contact-lens sales are non-returnable. Warranty covers manufacturing defects only; accidental damage is out of scope. Full policy at <span className="mono">bettervision.in/terms</span>.
        </div>
        <div style={{fontFamily:'var(--font-mono)', fontSize:9.5, color:'var(--ink-4)', textTransform:'uppercase', letterSpacing:'.14em', textAlign:'right'}}>
          Subject to Delhi<br/>jurisdiction
        </div>
      </div>
    );
  }

  if (variant === 'trust') {
    return (
      <div style={{borderTop:'1px solid var(--line)'}}>
        <div style={{padding:'12px 44px', display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap:14, borderBottom:'1px solid var(--line)'}}>
          {[
            ['ISO 9001:2015','Quality-managed since 2014'],
            ['BIS·IS 15754','Lens safety certified'],
            ['1-year warranty','On frames & lenses'],
            ['Free adjustment','Lifetime · any BV store']
          ].map(([h,s]) => (
            <div key={h}>
              <div style={{fontFamily:'var(--font-mono)', fontSize:9.5, textTransform:'uppercase', letterSpacing:'.1em', color: accent, fontWeight:600}}>{h}</div>
              <div style={{fontSize:10.5, color:'var(--ink-3)', marginTop:2}}>{s}</div>
            </div>
          ))}
        </div>
        <div style={{padding:'8px 44px', fontFamily:'var(--font-mono)', fontSize:9.5, color:'var(--ink-4)', letterSpacing:'.08em', textAlign:'center'}}>
          E&OE · bettervision.in · {docUrl}
        </div>
      </div>
    );
  }

  // legal (default)
  return (
    <div style={{padding:'10px 20px 24px', fontFamily:'var(--font-mono)', fontSize:9.5, color:'var(--ink-4)', textAlign:'center', letterSpacing:'.08em', borderTop:'1px solid var(--line)'}}>
      E&OE · Subject to Delhi jurisdiction · This is a system-generated document · {docUrl}
    </div>
  );
}

/* ═══════════════════════ Diagrams (SVG, technical style) ═══════════════════════ */
function EyeDiagram({ side='OD', sph='−2.50', cyl='−0.75', axis=175, accent='#C2410C' }) {
  // Top-down eye cross-section with cylinder-axis overlay
  return (
    <svg viewBox="0 0 160 100" style={{width:'100%', height:'auto', display:'block'}}>
      <defs>
        <pattern id={`hatch-${side}`} patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="4" stroke="currentColor" strokeWidth="0.4" opacity=".3"/>
        </pattern>
      </defs>
      {/* Eye outline */}
      <ellipse cx="80" cy="50" rx="52" ry="32" fill="none" stroke="currentColor" strokeWidth="0.8"/>
      {/* Cornea */}
      <path d="M 28 50 Q 14 50 28 38" fill="none" stroke="currentColor" strokeWidth="0.8"/>
      <path d="M 28 50 Q 14 50 28 62" fill="none" stroke="currentColor" strokeWidth="0.8"/>
      {/* Iris + pupil */}
      <circle cx="30" cy="50" r="8" fill={`url(#hatch-${side})`} stroke="currentColor" strokeWidth=".5"/>
      <circle cx="30" cy="50" r="3" fill="currentColor"/>
      {/* Lens (crystalline) */}
      <ellipse cx="44" cy="50" rx="3" ry="8" fill="none" stroke="currentColor" strokeWidth=".6"/>
      {/* Retina hatch */}
      <path d="M 115 28 Q 132 50 115 72" fill="none" stroke={accent} strokeWidth="1"/>
      {/* Labels */}
      <line x1="30" y1="18" x2="30" y2="28" stroke="currentColor" strokeWidth=".4"/>
      <text x="30" y="14" fontSize="5.5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".7">Cornea</text>
      <line x1="44" y1="66" x2="44" y2="72" stroke="currentColor" strokeWidth=".4"/>
      <text x="44" y="78" fontSize="5.5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".7">Lens</text>
      <line x1="115" y1="18" x2="120" y2="24" stroke={accent} strokeWidth=".4"/>
      <text x="115" y="14" fontSize="5.5" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent}>Retina</text>
      {/* Side label */}
      <text x="8" y="16" fontSize="8" fontFamily="var(--font-display)" fill="currentColor">{side}</text>
      {/* Rx values corner */}
      <g transform="translate(138, 88)">
        <text x="0" y="0" fontSize="5" fontFamily="var(--font-mono)" textAnchor="end" fill="currentColor" opacity=".6">SPH {sph} · CYL {cyl} · AX {axis}°</text>
      </g>
      {/* Axis indicator ring */}
      <g transform="translate(30, 50)">
        <circle r="12" fill="none" stroke={accent} strokeWidth=".4" strokeDasharray="1 1.5"/>
        <line x1={-12*Math.cos(axis*Math.PI/180)} y1={-12*Math.sin(axis*Math.PI/180)} x2={12*Math.cos(axis*Math.PI/180)} y2={12*Math.sin(axis*Math.PI/180)} stroke={accent} strokeWidth=".8"/>
      </g>
    </svg>
  );
}

function FrameDiagram({ accent='#C2410C' }) {
  // Eyewear front-view with measurements
  return (
    <svg viewBox="0 0 260 90" style={{width:'100%', height:'auto', display:'block'}}>
      {/* Left lens */}
      <path d="M 20 30 Q 20 20 30 20 L 100 20 Q 110 20 110 30 L 110 55 Q 110 65 100 65 L 30 65 Q 20 65 20 55 Z" fill="none" stroke="currentColor" strokeWidth="1"/>
      {/* Bridge */}
      <path d="M 110 32 Q 130 22 150 32" fill="none" stroke="currentColor" strokeWidth="1"/>
      {/* Right lens */}
      <path d="M 150 30 Q 150 20 160 20 L 230 20 Q 240 20 240 30 L 240 55 Q 240 65 230 65 L 160 65 Q 150 65 150 55 Z" fill="none" stroke="currentColor" strokeWidth="1"/>
      {/* Temples */}
      <path d="M 20 32 L 4 28" stroke="currentColor" strokeWidth="1" fill="none"/>
      <path d="M 240 32 L 256 28" stroke="currentColor" strokeWidth="1" fill="none"/>

      {/* A (eye size) */}
      <line x1="20" y1="74" x2="110" y2="74" stroke={accent} strokeWidth=".4"/>
      <line x1="20" y1="72" x2="20" y2="76" stroke={accent} strokeWidth=".4"/>
      <line x1="110" y1="72" x2="110" y2="76" stroke={accent} strokeWidth=".4"/>
      <text x="65" y="82" fontSize="6" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent}>A · 50</text>

      {/* DBL (bridge) */}
      <line x1="110" y1="74" x2="150" y2="74" stroke={accent} strokeWidth=".4"/>
      <line x1="110" y1="72" x2="110" y2="76" stroke={accent} strokeWidth=".4"/>
      <line x1="150" y1="72" x2="150" y2="76" stroke={accent} strokeWidth=".4"/>
      <text x="130" y="82" fontSize="6" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent}>DBL · 22</text>

      {/* B (vertical) */}
      <line x1="4" y1="20" x2="4" y2="65" stroke={accent} strokeWidth=".4"/>
      <line x1="2" y1="20" x2="6" y2="20" stroke={accent} strokeWidth=".4"/>
      <line x1="2" y1="65" x2="6" y2="65" stroke={accent} strokeWidth=".4"/>
      <text x="10" y="45" fontSize="6" fontFamily="var(--font-mono)" fill={accent}>B · 42</text>

      {/* OC height */}
      <circle cx="65" cy="45" r="1.2" fill={accent}/>
      <circle cx="195" cy="45" r="1.2" fill={accent}/>
      <line x1="65" y1="45" x2="65" y2="65" stroke={accent} strokeWidth=".3" strokeDasharray="1 1"/>
      <text x="65" y="12" fontSize="5" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent} opacity=".7">OC · 18</text>

      {/* Title */}
      <text x="130" y="8" fontSize="6" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".5" letterSpacing=".1em">FRAME MEASUREMENT</text>
    </svg>
  );
}

function PdDiagram({ pdOd=32.0, pdOs=31.5, accent='#C2410C' }) {
  return (
    <svg viewBox="0 0 220 70" style={{width:'100%', height:'auto', display:'block'}}>
      {/* Face outline (minimal) */}
      <path d="M 40 15 Q 20 30 40 55 Q 110 64 180 55 Q 200 30 180 15 Q 110 6 40 15 Z" fill="none" stroke="currentColor" strokeWidth=".5" opacity=".3"/>
      {/* Eyes */}
      <ellipse cx="78" cy="32" rx="12" ry="6" fill="none" stroke="currentColor" strokeWidth=".8"/>
      <ellipse cx="142" cy="32" rx="12" ry="6" fill="none" stroke="currentColor" strokeWidth=".8"/>
      <circle cx="78" cy="32" r="2.5" fill="currentColor"/>
      <circle cx="142" cy="32" r="2.5" fill="currentColor"/>
      {/* Nose bridge */}
      <path d="M 110 30 Q 106 40 108 46 L 112 46 Q 114 40 110 30" fill="none" stroke="currentColor" strokeWidth=".5" opacity=".4"/>
      {/* Center line */}
      <line x1="110" y1="8" x2="110" y2="62" stroke={accent} strokeWidth=".3" strokeDasharray="1 1"/>
      {/* Measurement arrows */}
      <line x1="78" y1="54" x2="110" y2="54" stroke={accent} strokeWidth=".6"/>
      <polygon points="78,54 82,52 82,56" fill={accent}/>
      <polygon points="110,54 106,52 106,56" fill={accent}/>
      <text x="94" y="64" fontSize="7" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent} fontWeight="600">{pdOd.toFixed(1)}</text>
      <line x1="110" y1="54" x2="142" y2="54" stroke={accent} strokeWidth=".6"/>
      <polygon points="110,54 114,52 114,56" fill={accent}/>
      <polygon points="142,54 138,52 138,56" fill={accent}/>
      <text x="126" y="64" fontSize="7" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent} fontWeight="600">{pdOs.toFixed(1)}</text>
      {/* Labels */}
      <text x="78" y="18" fontSize="5.5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".6">OD</text>
      <text x="142" y="18" fontSize="5.5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".6">OS</text>
      <text x="110" y="8" fontSize="5" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent} opacity=".7">midline</text>
      {/* Title */}
      <text x="10" y="10" fontSize="6" fontFamily="var(--font-mono)" fill="currentColor" opacity=".5" letterSpacing=".1em">PD · mm</text>
    </svg>
  );
}

function LensZonesDiagram({ accent='#C2410C' }) {
  // Progressive lens zones
  return (
    <svg viewBox="0 0 120 140" style={{width:'100%', height:'auto', display:'block'}}>
      {/* Lens outline */}
      <ellipse cx="60" cy="70" rx="48" ry="58" fill="none" stroke="currentColor" strokeWidth="1"/>
      {/* Distance zone */}
      <path d="M 12 60 Q 60 45 108 60 L 108 20 Q 60 5 12 20 Z" fill="currentColor" opacity=".05"/>
      <text x="60" y="32" fontSize="7" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" fontWeight="600">DISTANCE</text>
      <text x="60" y="42" fontSize="5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".6">Far view</text>
      {/* Corridor */}
      <path d="M 50 60 L 52 100 L 68 100 L 70 60 Z" fill={accent} opacity=".08" stroke={accent} strokeWidth=".3" strokeDasharray="2 2"/>
      <text x="60" y="82" fontSize="5" fontFamily="var(--font-mono)" textAnchor="middle" fill={accent}>corridor</text>
      {/* Near zone */}
      <path d="M 28 120 Q 60 128 92 120 L 92 100 Q 60 108 28 100 Z" fill="currentColor" opacity=".05"/>
      <text x="60" y="116" fontSize="7" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" fontWeight="600">NEAR</text>
      <text x="60" y="124" fontSize="5" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".6">Reading</text>
      {/* Peripheral (soft) */}
      <text x="18" y="70" fontSize="5" fontFamily="var(--font-mono)" fill="currentColor" opacity=".4">soft</text>
      <text x="90" y="70" fontSize="5" fontFamily="var(--font-mono)" fill="currentColor" opacity=".4">soft</text>
      {/* Fitting cross */}
      <circle cx="60" cy="60" r="2" fill={accent}/>
      <line x1="54" y1="60" x2="66" y2="60" stroke={accent} strokeWidth=".5"/>
      <line x1="60" y1="54" x2="60" y2="66" stroke={accent} strokeWidth=".5"/>
      <text x="74" y="62" fontSize="5" fontFamily="var(--font-mono)" fill={accent}>fitting cross</text>
      <text x="60" y="138" fontSize="6" fontFamily="var(--font-mono)" textAnchor="middle" fill="currentColor" opacity=".5" letterSpacing=".1em">PROGRESSIVE ZONES</text>
    </svg>
  );
}

/* ═══════════════════════ Mini-charts ═══════════════════════ */
function Sparkline({ data=[5200, 4800, 7100, 6300, 8900, 6800, 12400, 9200, 7100, 10200, 8400, 28110], accent='#C2410C', label='Customer spend, last 12 visits' }) {
  const w=220, h=48, pad=4;
  const min = Math.min(...data), max = Math.max(...data);
  const xs = data.map((_,i) => pad + i * (w - pad*2) / (data.length-1));
  const ys = data.map(v => h - pad - ((v-min)/(max-min)) * (h - pad*2));
  const path = xs.map((x,i) => (i===0 ? 'M' : 'L') + x + ' ' + ys[i]).join(' ');
  const area = path + ` L ${xs[xs.length-1]} ${h} L ${xs[0]} ${h} Z`;
  return (
    <div>
      <div style={{fontFamily:'var(--font-mono)', fontSize:9.5, textTransform:'uppercase', letterSpacing:'.12em', color:'var(--ink-4)', marginBottom:4}}>{label}</div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{width:'100%', height:h}}>
        <path d={area} fill={accent} opacity=".12"/>
        <path d={path} fill="none" stroke={accent} strokeWidth="1.2"/>
        {xs.map((x,i) => (
          <circle key={i} cx={x} cy={ys[i]} r={i===data.length-1 ? 2.6 : 1} fill={i===data.length-1 ? accent : 'var(--ink-4)'}/>
        ))}
        {/* last point label */}
        <text x={xs[xs.length-1]-4} y={ys[ys.length-1]-6} fontSize="7" fontFamily="var(--font-mono)" textAnchor="end" fill={accent} fontWeight="600">this visit</text>
      </svg>
      <div style={{display:'flex', justifyContent:'space-between', fontSize:9, fontFamily:'var(--font-mono)', color:'var(--ink-4)', marginTop:2}}>
        <span>Apr '25</span><span>Apr '26</span>
      </div>
    </div>
  );
}

function StockBar({ items=[
  { n:'Ray-Ban RB4171',  have:12, par:20 },
  { n:'Oakley OO9208',   have:6,  par:15 },
  { n:'Vogue VO5234',    have:18, par:25 },
  { n:'BV Pouch',        have:200,par:300 },
], accent='#C2410C' }) {
  return (
    <div>
      <div style={{fontFamily:'var(--font-mono)', fontSize:9.5, textTransform:'uppercase', letterSpacing:'.12em', color:'var(--ink-4)', marginBottom:6}}>Stock level after this transfer</div>
      <div style={{display:'grid', gap:4}}>
        {items.map(it => {
          const pct = Math.min(100, (it.have/it.par)*100);
          const low = pct < 60;
          return (
            <div key={it.n} style={{display:'grid', gridTemplateColumns:'110px 1fr 60px', gap:8, alignItems:'center', fontSize:10}}>
              <span style={{color:'var(--ink-3)'}}>{it.n}</span>
              <div style={{height:8, background:'var(--bg-sunk)', borderRadius:1, overflow:'hidden', position:'relative'}}>
                <div style={{position:'absolute', inset:0, width:`${pct}%`, background: low ? 'var(--warn, #a86c2a)' : accent}}/>
                <div style={{position:'absolute', top:0, bottom:0, left: '60%', width:1, background:'var(--ink-4)', opacity:.5}}/>
              </div>
              <span style={{fontFamily:'var(--font-mono)', fontVariantNumeric:'tabular-nums', fontSize:9.5, color:'var(--ink-4)', textAlign:'right'}}>{it.have}/{it.par}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ═══════════════════════ Dos & Don'ts annotation overlay ═══════════════════════
   Absolutely-positioned dotted callouts over the paper, pointing to key anchors.
   Each anchor element must carry data-anno="key-name".
*/
function Annotations({ items=[], visible=true, accent='#C2410C' }) {
  const [positions, setPositions] = React.useState([]);

  React.useEffect(() => {
    if (!visible) return;
    const measure = () => {
      const paper = document.querySelector('[data-doc]');
      if (!paper) return;
      const rect = paper.getBoundingClientRect();
      const out = items.map(it => {
        const el = paper.querySelector(`[data-anno="${it.anchor}"]`);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
          ...it,
          x: r.left - rect.left + r.width/2,
          y: r.top - rect.top + r.height/2,
          w: r.width,
          h: r.height,
          ax: r.left - rect.left,
          ay: r.top - rect.top,
        };
      }).filter(Boolean);
      setPositions(out);
    };
    measure();
    // re-measure after fonts / layout settle
    const t1 = setTimeout(measure, 150);
    const t2 = setTimeout(measure, 400);
    window.addEventListener('resize', measure);
    return () => { clearTimeout(t1); clearTimeout(t2); window.removeEventListener('resize', measure); };
  }, [items, visible]);

  if (!visible) return null;

  return (
    <div style={{position:'absolute', inset:0, pointerEvents:'none'}}>
      {positions.map((p, i) => {
        const kind = p.kind || 'do'; // 'do' | 'dont' | 'info'
        const color = kind === 'dont' ? '#b91c1c' : kind === 'info' ? '#1e40af' : '#166534';
        const side = p.side || 'right';
        const cardX = side === 'right' ? p.ax + p.w + 24 : side === 'left' ? p.ax - 184 : p.x - 90;
        const cardY = p.ay + (p.h/2) - 14;
        return (
          <React.Fragment key={i}>
            {/* Highlight box */}
            <div style={{position:'absolute', left:p.ax-3, top:p.ay-3, width:p.w+6, height:p.h+6, border:`1.5px dashed ${color}`, borderRadius:3, background: color + '10'}}/>
            {/* Leader line */}
            <svg style={{position:'absolute', inset:0, width:'100%', height:'100%'}}>
              <line
                x1={side==='right' ? p.ax+p.w+3 : p.ax-3}
                y1={p.ay + p.h/2}
                x2={side==='right' ? cardX : cardX+180}
                y2={cardY + 14}
                stroke={color} strokeWidth="1" strokeDasharray="2 2"
              />
            </svg>
            {/* Callout card */}
            <div style={{
              position:'absolute', left:cardX, top:cardY, width:180,
              background:'#fff', border:`1.5px solid ${color}`, borderRadius:4,
              padding:'6px 9px', boxShadow:'0 4px 10px -4px rgba(0,0,0,.2)',
              fontFamily:'var(--font-sans)', fontSize:10, lineHeight:1.4
            }}>
              <div style={{fontFamily:'var(--font-mono)', fontSize:8.5, textTransform:'uppercase', letterSpacing:'.12em', color, fontWeight:600, marginBottom:2}}>
                {kind === 'do' ? '✓ Do' : kind === 'dont' ? '✗ Don\'t' : 'ⓘ Note'} · {p.title}
              </div>
              <div style={{color:'var(--ink-2)'}}>{p.body}</div>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

/* ═══════════════════════ Small helpers ═══════════════════════ */
function MiniQR({ label }) {
  const cells = [];
  const seed = 'BVQR';
  for (let y=0;y<12;y++) for (let x=0;x<12;x++) {
    const h = (x*31 + y*17 + seed.charCodeAt((x+y)%seed.length)) % 7;
    cells.push(h > 3 ? 1 : 0);
  }
  const mark = (sx, sy) => { for (let y=0;y<5;y++) for (let x=0;x<5;x++) {
    const border = x===0||y===0||x===4||y===4; const center = x>=1&&x<=3&&y>=1&&y<=3;
    cells[(sy+y)*12 + (sx+x)] = border || center ? 1 : 0;
  } };
  mark(0,0); mark(7,0); mark(0,7);
  return (
    <div style={{textAlign:'center'}}>
      <div style={{display:'grid', gridTemplateColumns:'repeat(12, 3px)', gridAutoRows:'3px', width:48, height:48, padding:2, background:'#fff', border:'1px solid var(--ink-5)'}}>
        {cells.map((c,i) => <div key={i} style={{background: c ? 'var(--ink)' : 'transparent'}} />)}
      </div>
      {label && <div style={{fontFamily:'var(--font-mono)', fontSize:8, color:'var(--ink-4)', marginTop:3, textTransform:'uppercase', letterSpacing:'.1em'}}>{label}</div>}
    </div>
  );
}

Object.assign(window, {
  Header, Footer, EyeDiagram, FrameDiagram, PdDiagram, LensZonesDiagram,
  Sparkline, StockBar, Annotations, MiniQR, BV_INFO
});
