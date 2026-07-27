// ============================================================================
// Pixel avatar set generator (v3) — 生成 40 帧 RGB565 表情包 / builds a 40-frame sheet
//
// 用法 / Usage:
//   node gen_avatar_v3.js      →  ./avatar_set.bin   (1,536,000 bytes)
//
// 输出格式 / Output format
//   每帧 160x120 像素、RGB565 **little-endian**（低字节在前）→ 160*120*2 = 38,400 字节/帧
//   40 帧首尾相接、无文件头、无对齐填充 → 40 * 38,400 = 1,536,000 字节
//   Each frame is 160x120 px, RGB565 little-endian (low byte first) = 38,400 bytes.
//   40 frames are concatenated back to back — no header, no padding = 1,536,000 bytes.
//
// 帧布局 / Frame layout（顺序不能改 / order is fixed）
//   索引 0-15   16 张脸 A 帧 / 16 face frames, animation frame A
//   索引 16-18  3 组眼睛：open / half / closed
//   索引 19-23  5 组嘴型：closed / half / open / E / U
//   索引 24-39  16 张脸 B 帧（动画第二帧）/ 16 face frames, animation frame B
//
// 脸的槽位顺序必须严格对应固件的 FaceNameToIndex：
// Face slot order must match the firmware's FaceNameToIndex exactly:
//   0 idle        1 happy       2 thinking    3 sad
//   4 surprised   5 embarrassed 6 kiss        7 cry
//   8 sleep       9 hug        10 smug       11 heart_eyes
//  12 mischief   13 angry      14 worry      15 puzzled
//
// ⚠ 重要经验 / Hard-won lesson:
//   嘴型帧（索引 19-23）**必须自带眼睛**。这些帧是整脸重绘，不是局部贴图；
//   如果只画嘴不画眼，设备说话时每次切换嘴型，眼睛就会消失一瞬间（看上去像瞎了）。
//   The five mouth frames MUST include the eyes. They are full-face redraws, not sprite
//   overlays — a mouth-only frame makes the eyes vanish every time the mouth changes
//   while the device is speaking. Same applies to the three eye frames: draw the whole face.
//
// ⚠ 40 帧包需要给上游网关打补丁放开尺寸校验，见 ../patches/README.md（补丁 #2）。
//   A 40-frame sheet requires patching the upstream gateway's size check — see ../patches/README.md.
// ============================================================================

const { PALETTE, BASE } = require("./sprites.js");
const { NEW } = require("./new_expressions.js");
const fs = require("fs");

// 表情包画布：160x120，逻辑网格放大 5 倍，偏移 (15,5) 让身体居中
// Sheet canvas: 160x120; logical grid scaled 5x with a (15,5) offset to center the body.
const W = 160, H = 120, CELL = 5, OX = 15, OY = 5;

// #RRGGBB → RGB565 的两个字节，低字节在前 / returns [lowByte, highByte]
function rgb565le(hex) {
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  const v = ((r>>3)<<11)|((g>>2)<<5)|(b>>3); return [v&0xff, v>>8];
}

// 把一组 rect 渲染成一帧。dim=true 时把小灯的 Y/y 换成暗色 M（睡眠态）。
// Render a rect list into one frame. dim=true swaps the lamp colors Y/y for the dim M.
function render(rects, dim) {
  const buf = Buffer.alloc(W*H*2); const [bl,bh] = rgb565le("#111111");   // 背景 / background
  for (let i=0;i<W*H;i++){buf[i*2]=bl;buf[i*2+1]=bh;}
  for (const [x,y,w,h,c] of rects) {
    let col = PALETTE[c]; if (dim && (c==="Y"||c==="y")) col = PALETTE.M;
    const [lo,hi] = rgb565le(col);
    const px=OX+x*CELL, py=OY+y*CELL, pw=w*CELL, ph=h*CELL;
    for (let yy=Math.max(0,py);yy<Math.min(H,py+ph);yy++)
      for (let xx=Math.max(0,px);xx<Math.min(W,px+pw);xx++){const p=(yy*W+xx)*2;buf[p]=lo;buf[p+1]=hi;}
  }
  return buf;
}

const B  = (r) => render([...BASE, ...r]);        // 画身体 + overlay / body + overlay
const Bd = (r) => render([...BASE, ...r], true);  // 同上但小灯调暗 / same, lamp dimmed
const EYES = [[10,9,1,2,"K"],[16,9,1,2,"K"]];     // 标准睁眼 / standard open eyes
const newById = Object.fromEntries(NEW.map(e => [e.id, e]));

// 每个槽位是 [A帧, B帧] / each slot is [frameA, frameB]
const FACES = [
  // 0 idle: 睁眼 / 睁眼 + 灯光晕呼吸
  [B(EYES), render([...BASE,[9,0,1,1,"y"],[15,0,1,1,"y"],[10,1,1,1,"y"],[14,1,1,1,"y"],...EYES])],
  // 1 happy: 笑 / 整体上跳 1 格
  [B([[9,9,1,1,"K"],[10,8,1,1,"K"],[11,9,1,1,"K"],[15,9,1,1,"K"],[16,8,1,1,"K"],[17,9,1,1,"K"],[8,11,2,1,"P"],[16,11,2,1,"P"]]),
   render([...BASE.map(([x,y,w,h,c])=>[x,y-1,w,h,c]),[9,8,1,1,"K"],[10,7,1,1,"K"],[11,8,1,1,"K"],[15,8,1,1,"K"],[16,7,1,1,"K"],[17,8,1,1,"K"],[8,10,2,1,"P"],[16,10,2,1,"P"]])],
  // 2 thinking: 三个点，A 帧亮左边 / B 帧亮右边（依次闪）
  [B([[10,9,1,1,"K"],[16,9,1,1,"K"],[11,12,4,1,"K"],[19,3,1,1,"W"],[21,3,1,1,"G"],[23,3,1,1,"G"]]),
   B([[10,9,1,1,"K"],[16,9,1,1,"K"],[11,12,4,1,"K"],[19,3,1,1,"G"],[21,3,1,1,"W"],[23,3,1,1,"W"]])],
  // 3 sad: 泪在 y11-14 / 泪流到 y13-16
  [B([...EYES,[12,12,2,1,"K"],[10,11,1,4,"B"],[16,11,1,4,"B"]]),
   B([...EYES,[12,12,2,1,"K"],[10,13,1,3,"B"],[16,13,1,3,"B"],[10,11,1,1,"b"],[16,11,1,1,"b"]])],
  // 4 surprised: 睁大 / 整体后仰 1 格
  [B([[9,9,2,2,"K"],[15,9,2,2,"K"],[12,12,2,2,"K"]]),
   render([...BASE.map(([x,y,w,h,c])=>[x,y-1,w,h,c]),[9,8,2,2,"K"],[15,8,2,2,"K"],[12,11,2,2,"K"]])],
  // 5 embarrassed: 腮红 / 腮红更宽
  [B([...EYES,[8,11,3,1,"P"],[15,11,3,1,"P"],[12,12,2,1,"K"]]),
   B([...EYES,[7,11,4,1,"P"],[15,11,4,1,"P"],[12,12,2,1,"K"]])],
];

// 6-14: 从 new_expressions.js 按固定 id 顺序取（顺序必须匹配 FaceNameToIndex）
// Slots 6-14 come from new_expressions.js in this exact id order.
["kiss","cry","sleep","hug","smug","heart_eyes","mischief","angry","worry"].forEach(id=>{
  const e = newById[id];
  const fn = e.lampDim ? Bd : B;      // lampDim 的表情用暗灯渲染 / dim lamp when requested
  FACES.push([fn(e.A), fn(e.B)]);
});

// 15 puzzled: 绿色问号，A/B 帧轻微左右摇摆 / green question mark, wobbling between frames
FACES.push([
  B([...EYES,[21,2,2,1,"E"],[23,3,1,1,"E"],[22,4,1,1,"E"],[22,6,1,1,"E"]]),
  B([...EYES,[20,2,2,1,"E"],[22,3,1,1,"E"],[21,4,1,1,"E"],[21,6,1,1,"E"]]),
]);

if (FACES.length !== 16) throw new Error("face count mismatch / 脸数不对: " + FACES.length);

// --- 眼睛 3 帧 + 嘴 5 帧 ---------------------------------------------------
// 全部是"整脸"帧：都带身体、都带眼睛，只替换需要变化的那一处。
// All eight are FULL-FACE frames: body + eyes always drawn, only the varying part swapped.
// 见文件头的重要经验：嘴型帧不带眼睛会让说话时眼睛消失。
const eyesOpen   = B(EYES);
const eyesHalf   = B([[10,10,1,1,"K"],[16,10,1,1,"K"]]);
const eyesClosed = B([[9,10,2,1,"K"],[15,10,2,1,"K"]]);

const mClosed = B(EYES);                          // 闭嘴 / mouth closed
const mHalf   = B([...EYES,[12,12,2,1,"K"]]);     // 微张 / half open
const mOpen   = B([...EYES,[11,12,4,2,"K"]]);     // 大张 / wide open
const mE      = B([...EYES,[11,12,4,1,"K"]]);     // "E" 音口型 / "E" viseme
const mU      = B([...EYES,[12,12,2,2,"K"]]);     // "U" 音口型 / "U" viseme

// --- 拼装 / Assemble -------------------------------------------------------
// 前 24 帧是上游网关认识的标准布局（16 脸 + 3 眼 + 5 嘴），
// 后 16 帧是本项目扩展的 B 帧，用于两帧循环动画。
// The first 24 frames are the layout upstream expects; the trailing 16 are this
// project's animation-B extension.
const facesA = FACES.map(f => f[0]);
const facesB = FACES.map(f => f[1]);
const out = Buffer.concat([
  ...facesA,                                  // 0-15   16 face A
  eyesOpen, eyesHalf, eyesClosed,             // 16-18  3 eyes
  mClosed, mHalf, mOpen, mE, mU,              // 19-23  5 mouths
  ...facesB,                                  // 24-39  16 face B (animation)
]);

const dest = __dirname + "/avatar_set.bin";
fs.writeFileSync(dest, out);
console.log(dest, out.length, out.length === 40*38400 ? "OK (40 frames)" : "SIZE ERROR");
