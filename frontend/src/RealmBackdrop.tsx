import { useId } from 'react';
import type { Role } from './api';
import './cinematic.css';

export type CinematicScene = 'clock' | 'rebellion' | 'camp';

/** Local painted geometry: no remote assets, random values, canvas or animation loop. */
export function SceneIllustration({ scene }: { scene: CinematicScene }) {
  const id = useId().replace(/:/g, '');
  const link = (name: string) => `url(#${id}-${name})`;
  const camp = scene === 'camp';
  const fallen = scene === 'rebellion';
  return <svg className={`realm-art realm-art--${scene}`} viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={`${id}-sky`} x2="0.35" y2="1">
        <stop stopColor={camp ? '#14272f' : fallen ? '#39272b' : '#102e37'} />
        <stop offset=".56" stopColor={camp ? '#304650' : fallen ? '#6d3f37' : '#476167'} />
        <stop offset="1" stopColor={camp ? '#121e25' : '#111c24'} />
      </linearGradient>
      <linearGradient id={`${id}-stone`} x2=".6" y2="1"><stop stopColor="#3f5155" /><stop offset="1" stopColor="#172831" /></linearGradient>
      <linearGradient id={`${id}-tower`} x2="1" y2=".2"><stop stopColor="#1a2b34" /><stop offset=".48" stopColor="#40525a" /><stop offset=".53" stopColor="#576567" /><stop offset="1" stopColor="#21333c" /></linearGradient>
      <radialGradient id={`${id}-aura`}><stop stopColor={fallen ? '#d38664' : '#8ae5df'} stopOpacity=".47" /><stop offset="1" stopColor="#67cfc7" stopOpacity="0" /></radialGradient>
      <radialGradient id={`${id}-firelight`}><stop stopColor="#e9a055" stopOpacity=".6" /><stop offset=".4" stopColor="#bb6c3d" stopOpacity=".2" /><stop offset="1" stopColor="#a85d3b" stopOpacity="0" /></radialGradient>
      <linearGradient id={`${id}-cloth`} x2="1" y2="1"><stop stopColor="#746653" /><stop offset="1" stopColor="#2d383b" /></linearGradient>
      <linearGradient id={`${id}-water`} x2="0" y2="1"><stop stopColor="#97c7bd" stopOpacity=".6" /><stop offset="1" stopColor="#4d8e92" stopOpacity="0" /></linearGradient>
    </defs>
    <rect width="1440" height="900" fill={link('sky')} />
    <g className="realm-stars" fill="#d8dfd2">
      {[ [106,132], [273,74], [438,142], [586,53], [722,105], [1094,65], [1280,120], [1370,210], [1210,225], [495,232], [327,221], [81,270] ].map(([x,y],i)=><circle key={x} cx={x} cy={y} r={i%3===0?1.5:.9} opacity={.3+i%3*.17} />)}
    </g>
    <circle cx={camp ? 1080 : 925} cy={camp ? 184 : 230} r={camp ? 69 : 225} fill={camp ? '#b8c3bd' : link('aura')} opacity={camp ? .18 : 1} />
    <g className="realm-far-mountains">
      <path d="M0 410 95 311 174 369 276 248 400 400 548 276 704 398 835 278 962 412 1090 280 1216 363 1310 246 1440 367V900H0Z" fill="#253d46" opacity=".7" />
      <path d="m0 492 126-121 101 54 188-112 142 188 151-90 160 54 168-122 147 146 166-79 92 46 99-74V900H0Z" fill="#24353f" />
    </g>
    <g className="realm-mist realm-mist--far" fill="#acc1bd" opacity=".09"><path d="M-100 404Q200 350 530 400T1540 403v24Q1110 454 710 419T-100 449Z" /><path d="M-100 491Q330 455 700 476T1540 469v30Q1040 533 600 500T-100 521Z" /></g>
    {!camp && <>
      <g className="realm-capital">
        <g fill="#1d3039" stroke="#60716c" strokeOpacity=".3">
          <path d="M202 555V402h29v-51h35v51h27v153m807 2V369h24v-59h36v59h30v188M340 540V316h28v-30h35v30h23v224m827 5V438h18v-45h35v45h28v107" />
          <path d="M543 541V344h34v-88h21v-43l18-21 17 21v43h21v88h35v197" />
          <path d="M1035 546V320h-24v-48h-15v-47l-16-24-17 24v47h-18v48h-24v226" />
          <path d="m730 501 22-172h30v-32h31v32h29l20 172" />
        </g>
        <path d="M60 604V546h420v-26h520v27h400v80H60Z" fill="#2e4249" />
        <g fill="#a4beb1" opacity={fallen ? .13 : .62}>
          {[246,378,592,623,772,801,970,1137,1291].map((x,i)=><path key={x} d={`M${x} ${i%2?386:449}v19h6v-19z`} />)}
        </g>
        <g className="realm-arcane-lines" fill="none" stroke={fallen ? '#c67b63' : '#81c7bf'} strokeWidth="2" opacity=".53">
          <path d="M925 311v225H609v55H250v77M925 536h211v61h165v67M925 536v103H764v47" />
          <circle cx="609" cy="536" r="7" /><circle cx="1136" cy="536" r="7" /><circle cx="764" cy="639" r="7" />
        </g>
        <g className="realm-clock-tower">
          <path d="m849 578 17-324h123l19 324Z" fill={link('tower')} stroke="#8a8873" strokeOpacity=".45" />
          <path d="M857 255h142v-19H857zm13-31 15-43h83l17 43zm10-45 47-81 47 81Z" fill="#293e45" stroke="#7c8377" strokeOpacity=".5" />
          <path d="M920 97V72h15v25m-43 281h13v143h-13m57-143h13v143h-13" fill="#151f29" />
          <path d="m874 551 52-27 67 27v27H874" fill="#45575b" />
          <circle cx="927" cy="303" r="71" fill="#152d35" stroke="#b6a47a" strokeWidth="3" />
          <circle cx="927" cy="303" r="61" fill="none" stroke="#a7b8a2" strokeWidth="1" opacity=".7" />
          <g stroke="#b8ab83" strokeWidth="2">{Array.from({length:12},(_,i)=><path key={i} d="M927 247v8" transform={`rotate(${i*30} 927 303)`} />)}</g>
          <g className="realm-clock-hands" stroke="#e9d7a6" strokeWidth="3" strokeLinecap="round"><path d="M927 303v-43m0 43 29-18" /></g>
          <circle cx="927" cy="303" r="7" fill="#d7e6c9" />
          <path className="realm-thirteenth-mark" d="m922 224 5-11 5 11-5 11Z" fill="#c1fff0" />
        </g>
      </g>
      <g className="realm-aqueduct" fill={link('stone')} stroke="#7a8477" strokeOpacity=".25">
        <path fillRule="evenodd" d="M0 582H1440V735H0Zm60 153v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Zm141 0v-73a40 40 0 0 1 80 0v73Z" />
        <path d="M0 575H1440v12H0zm0 29H1440" />
        {!fallen && <g className="realm-water" stroke="#b7e5d5" strokeWidth="2" opacity=".3"><path d="M10 591h1420M10 596h1420" /><path d="M1140 607v132m7-132v135m7-135v128" /></g>}
        {fallen && <><path d="m729 571-23 47 27 30-37 35 20 54h141l-16-53 20-33-23-35 7-44" fill="#182831" stroke="none" /><path d="m705 757 33-20 24 14-12 25m49-27 30 7-7 20-30-6" fill="#4b5150" /></>}
      </g>
      <g fill="#192932" stroke="#667267" strokeOpacity=".25"><path d="M1100 754V654h134v100m-117-100 28-37h54l24 37m-72-37v-62h24v62" /><path d="M1130 754v-36a25 25 0 0 1 50 0v36Z" fill={fallen ? '#111f27' : '#be8d5b'} />
        {!fallen && <path className="realm-furnace" d="m1141 749-3-17 12-21 6 17 9-9 7 30Z" fill="#f5c083" stroke="none" />}
      </g>
      {fallen && <g className="realm-fallen-banner"><path d="m1020 566 64 41-30 11-12 35-33-29Z" fill="#7a4e46" /><path d="m1009 556 42 130" stroke="#967954" strokeWidth="4" /><path d="m989 738 14-37 16 37m-8-35v-17" fill="#101d26" /></g>}
    </>}
    {camp && <g className="realm-camp">
      <path d="M738 613 852 470l124 145Z" fill={link('cloth')} stroke="#8e8a73" strokeOpacity=".7" /><path d="m851 473 3 140h101Z" fill="#41453d" /><path d="m838 552-52 61h54Z" fill="#171f25" /><path d="m852 469-3-37m-116 184-13 19m254-20 14 19" stroke="#8f8266" strokeWidth="3" />
      <path d="m1129 633 77-101 97 101Z" fill="#4d5550" stroke="#8e8a73" strokeOpacity=".45" /><path d="m1207 535-1 98h76Z" fill="#343e3c" /><path d="m1206 564-34 69h36Z" fill="#17232a" />
      <g stroke="#1a2c34" strokeWidth="9"><path d="M1304 653V318m-5 63-53 44m54 24 60-53m-61 105-78-41m79 92 94-68M674 629V393m0 35-52 34m53 11 47-34m-48 91-67-26" /></g>
      <path d="m1295 298-63 115 46-16-71 103 71-24-64 99 154-4-65-97 58 21-55-98 44 15Z" fill="#20343b" />
      <path d="m665 365-55 96 44-14-63 91 134-1-56-84 37 10Z" fill="#243a40" />
      <path d="M818 689q195-69 389 0l-59 78H853Z" fill="#34413f" />
      <ellipse cx="1011" cy="660" rx="211" ry="146" fill={link('firelight')} className="realm-firelight" />
      <ellipse cx="1011" cy="700" rx="71" ry="18" fill="#151e22" />
      <g stroke="#967a58" strokeWidth="10" strokeLinecap="round"><path d="m977 704 69-18m-61 0 52 23" /></g>
      <g className="realm-flame"><path d="M980 690q-15-20 9-46 4 15 12 12-7-25 13-49-2 28 14 42 4-12 13-17-5 25 3 37 11 32-24 32Z" fill="#d58a4e" /><path d="M995 693q-7-16 9-33l8-16q-1 18 12 30 8 18-10 22Z" fill="#f4ce82" /></g>
      <g className="realm-camp-five" fill="#15252e" stroke="#8a8d78" strokeWidth="1.2">
        <path d="m760 673 14-41 18-8 17 10 15 39Zm17-44 7-24 17 26Z" />
        <path d="m844 692 5-45 18-13 19 11 12 47Zm8-50 1-23h24v24Zm-10 15-11 7v29l16-4" />
        <path d="m1073 697 12-49 18-12 17 14 11 47Zm19-59-12-17m12 5-2-15m18 26 10-17m-9 8 3-19" />
        <path d="m1151 694 5-47 19-11 21 11 11 47Zm12-51v-23h24v23m17-19v70m-6-64h14v-13h-14Z" />
        <path d="m1242 702 9-49 17-13 15 13 16 49Zm10-48 6-26h17l7 26" /><ellipse cx="1267" cy="620" rx="16" ry="4" fill="none" stroke="#b7ac8b" />
      </g>
      <g className="realm-fragment"><path d="m945 582 14-28 19 22-8 26Z" fill="#82c5bd" /><path d="m959 554-1 33 12 15" fill="none" stroke="#d9f5dd" /><ellipse cx="959" cy="616" rx="22" ry="4" fill="#67a3a1" opacity=".3" /></g>
    </g>}
    <g className="realm-ground"><path d="M0 755q225-47 429 14t433-12 578-22V900H0Z" fill="#14252d" /><path d="M0 805q209-45 450 15t458-13 532-1v94H0Z" fill="#102029" />
      <path d="m904 900 99-171 26 2-25 169Z" fill="#797c68" opacity=".12" />
      <path d="M0 900V491l42 99 35-13-22 31 61 87-50-9 83 101-80-23 67 136Zm1440 0V480l-48 115-29-8 25 32-65 103 47-11-80 109 78-26-55 106Z" fill="#0c1b24" />
    </g>
    <g className="realm-mist realm-mist--near" fill="#b1b8a2" opacity=".06"><path d="M-100 739q300-49 690 8t950-16v32q-510 65-965 13T-100 787Z" /></g>
    <g className="realm-embers" fill={fallen ? '#b98163' : '#edb86f'} opacity={camp || fallen ? .7 : .15}>{Array.from({length:14},(_,i)=><circle key={i} cx={740+(i*73)%600} cy={490+(i*47)%290} r={i%3===0?2:1} style={{animationDelay:`${-i*.7}s`}} />)}</g>
  </svg>;
}

export default function RealmBackdrop({ scene = 'camp', role }: { scene?: 'camp' | 'adventure'; role?: Role }) {
  return <div className={`realm-backdrop realm-backdrop--${scene}${role ? ` realm-backdrop--${role}` : ''}`} aria-hidden="true">
    <SceneIllustration scene={scene === 'camp' ? 'camp' : 'clock'} />
    <div className="realm-backdrop-veil" />
  </div>;
}
