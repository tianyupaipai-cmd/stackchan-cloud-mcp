// Pixel avatar set — 素材定义 / sprite definitions
//
// 风格：粗颗粒像素螃蟹（pixel crab），常驻标识 = 头顶一盏暖黄小灯。
// Style: chunky pixel crab with a persistent warm-yellow lamp above its head.
//
// 数据结构 / Data model
//   每个 sprite = 一组填充矩形 [x, y, w, h, colorKey]
//   Each sprite is a list of fill-rects: [x, y, w, h, colorKey]
//   坐标是 26x22 的**逻辑网格**（不是像素），左上角为 (0,0)，x 向右、y 向下。
//   Coordinates are on a 26x22 LOGICAL grid (not pixels); origin top-left, x→right, y→down.
//
// 两套渲染尺度 / Two render scales
//   - 设备固件全屏绘制：屏幕 320x240，scale=10 → 260x220，居中偏移 (30, 10)
//   - 本仓库的 gen_avatar_v3.js 生成表情包位图：160x120，scale=5，偏移 (15, 5)
//   Firmware full-screen: 320x240 canvas, scale 10, offset (30,10).
//   gen_avatar_v3.js sprite sheet: 160x120 canvas, scale 5, offset (15,5).

// ===== 调色板 / Palette =====
// 色键（单字母，大小写敏感）→ #RRGGBB。渲染时转成 RGB565 little-endian。
// Color key (case-sensitive single letter) → #RRGGBB, converted to RGB565 LE at render time.
const PALETTE = {
  O: "#DD7E57", // 身体主橙 / body orange
  K: "#1A1210", // 眼睛/嘴 近黑 / eyes + mouth (near-black)
  Y: "#F5C042", // 小灯/火焰黄 / lamp + flame yellow
  y: "#F5D042", // 灯光晕（呼吸闪烁）/ lamp glow (breathing blink)
  M: "#8A6B2A", // 小灯调暗色（睡眠态）/ dimmed lamp (sleep state)
  G: "#8A8A85", // 灰：灯杆/杂物 / grey: lamp pole, props
  W: "#FFFFFF", // 白 / white
  B: "#7EB8E8", // 水/眼泪蓝 / water + tear blue
  b: "#6AA8E8", // 深一档蓝 / darker blue
  P: "#F2A0B5", // 腮红/浅粉心 / blush, light pink heart
  R: "#E8506A", // 爱心红 / heart red
  F: "#F0862E", // 火焰橙/瓶盖 / flame orange, bottle cap
  E: "#5CB85C", // 绿：问号/对勾 / green: question mark, check
  V: "#AFA9EC", // 紫：氛围光 / purple: ambient light
  X: "#F0C244", // 星星/蛋黄/黄色小物 / star, yolk, yellow props
  S: "#8FC1EC", // 浅蓝：被子/泡泡 / light blue: quilt, bubbles
  N: "#B08968", // 棕：床框/木头 / brown: bed frame, wood
  D: "#2C2C2A", // 深灰：屏幕外壳 / dark grey: screen bezel
  d: "#5F5E5A", // 中灰：手柄/金属 / mid grey: controller, metal
  C: "#F5E6C8", // 奶油色：食物/热气 / cream: food, steam
};

// ===== 基础身体 / Base body =====
// 除 noBase 的 sprite 外，所有帧都先画 BASE，再叠各自的 overlay。
// Every frame draws BASE first, then its own overlay rects (unless the sprite sets noBase).
const BASE = [
  [10, 0, 1, 1, "y"], [14, 0, 1, 1, "y"],            // 灯光晕（呼吸动画：透明度 0.4~1.0 循环）/ lamp glow
  [11, 1, 3, 2, "Y"],                                // 小灯 / lamp
  [12, 3, 1, 4, "G"],                                // 灯杆 / lamp pole
  [6, 7, 14, 7, "O"],                                // 身体 / body
  [4, 9, 2, 3, "O"], [20, 9, 2, 3, "O"],             // 左右钳 / claws
  [8, 14, 1, 2, "O"], [11, 14, 1, 2, "O"],           // 腿 / legs
  [15, 14, 1, 2, "O"], [18, 14, 1, 2, "O"],
];
// 设计定稿：脚下的水波元素已移除，常驻标识只保留头顶小灯。
// Final design: the water ripple under the feet was removed; the head lamp is the only persistent motif.

// 默认睁眼 / default open eyes
const EYES_DEFAULT = [[10, 9, 1, 2, "K"], [16, 9, 1, 2, "K"]];

// ===== 表情 / Expressions（深色背景 #111111）=====
// anim 字段只是设计说明，供固件/上层动画参考，渲染时不使用。
// The `anim` field is documentation for the animation layer; the renderer ignores it.
const EXPRESSIONS = [
  { id: "idle", name: "待机 / idle", anim: "每3-5秒眨眼（眼睛变1px高一帧）", rects: [...EYES_DEFAULT] },

  { id: "happy", name: "开心 / happy", anim: "身体上下轻跳1px", rects: [
    [9, 9, 1, 1, "K"], [10, 8, 1, 1, "K"], [11, 9, 1, 1, "K"],
    [15, 9, 1, 1, "K"], [16, 8, 1, 1, "K"], [17, 9, 1, 1, "K"],
    [8, 11, 2, 1, "P"], [16, 11, 2, 1, "P"],
  ]},

  { id: "love", name: "心动 / love", anim: "小爱心向上飘出淡出", rects: [
    [8, 8, 1, 1, "P"], [10, 8, 1, 1, "P"], [8, 9, 3, 1, "P"], [9, 10, 1, 1, "P"],
    [15, 8, 1, 1, "P"], [17, 8, 1, 1, "P"], [15, 9, 3, 1, "P"], [16, 10, 1, 1, "P"],
    [3, 3, 1, 1, "R"], [5, 3, 1, 1, "R"], [3, 4, 3, 1, "R"], [4, 5, 1, 1, "R"],
    [20, 2, 1, 1, "R"], [22, 2, 1, 1, "R"], [20, 3, 3, 1, "R"], [21, 4, 1, 1, "R"],
  ]},

  { id: "shy", name: "害羞 / shy", anim: "腮红闪烁", rects: [
    ...EYES_DEFAULT,
    [8, 11, 3, 1, "P"], [15, 11, 3, 1, "P"],
  ]},

  { id: "wink", name: "眨眼 / wink", anim: "静态2秒回待机", rects: [
    [10, 9, 1, 2, "K"], [15, 10, 2, 1, "K"],
    [21, 4, 1, 1, "X"], [23, 6, 1, 1, "X"],
  ]},

  { id: "sleepy", name: "睡觉 / sleepy", anim: "zzz逐个浮现，小灯换M色调暗", lampDim: true, rects: [
    [9, 10, 2, 1, "K"], [15, 10, 2, 1, "K"],
    [19, 4, 2, 1, "b"], [20, 5, 1, 1, "b"], [19, 6, 2, 1, "b"],
    [22, 1, 2, 1, "b"], [23, 2, 1, 1, "b"], [22, 3, 2, 1, "b"],
  ]},

  { id: "sad", name: "委屈 / sad", anim: "眼泪往下流动", rects: [
    ...EYES_DEFAULT,
    [12, 12, 2, 1, "K"],
    [10, 11, 1, 4, "B"], [16, 11, 1, 4, "B"],
  ]},

  { id: "angry", name: "生气 / angry", anim: "白色气团一鼓一鼓", rects: [
    [9, 10, 1, 1, "K"], [10, 9, 1, 1, "K"],
    [16, 9, 1, 1, "K"], [17, 10, 1, 1, "K"],
    [20, 3, 2, 1, "W"], [19, 4, 4, 1, "W"], [20, 5, 3, 1, "W"],
  ]},

  { id: "surprised", name: "惊讶 / surprised", anim: "整体后仰1px再回", rects: [
    [9, 9, 2, 2, "K"], [15, 9, 2, 2, "K"],
    [22, 2, 1, 3, "X"], [22, 6, 1, 1, "X"],
  ]},

  { id: "thinking", name: "思考 / thinking", anim: "三个点依次亮（打字/想事）", rects: [
    // 眼睛微微看向上方，嘴抿着 / eyes glance up, mouth pressed flat
    [10, 9, 1, 1, "K"], [16, 9, 1, 1, "K"],
    [11, 12, 4, 1, "K"],
    // 右上角三个点：A帧亮左一个，B帧点右移，模拟依次亮
    // Three dots top-right: frame A lights the left dot, frame B shifts right (sequential blink)
    [19, 3, 1, 1, "G"], [21, 3, 1, 1, "G"], [23, 3, 1, 1, "G"],
  ]},

  { id: "speechless", name: "无语 / speechless", anim: "三个点依次亮", rects: [
    [9, 10, 2, 1, "K"], [15, 10, 2, 1, "K"],
    [20, 3, 1, 1, "G"], [22, 3, 1, 1, "G"], [24, 3, 1, 1, "G"],
  ]},

  { id: "dizzy", name: "晕 / dizzy", anim: "身体左右摇摆，星星绕圈", rects: [
    [9, 9, 1, 1, "K"], [11, 9, 1, 1, "K"], [10, 10, 1, 1, "K"], [9, 11, 1, 1, "K"], [11, 11, 1, 1, "K"],
    [15, 9, 1, 1, "K"], [17, 9, 1, 1, "K"], [16, 10, 1, 1, "K"], [15, 11, 1, 1, "K"], [17, 11, 1, 1, "K"],
    [20, 2, 2, 1, "X"], [19, 3, 1, 1, "X"], [22, 3, 1, 1, "X"],
  ]},

  { id: "cheer", name: "加油 / cheer", anim: "火苗上下跳动", rects: [
    ...EYES_DEFAULT,
    [21, 4, 2, 3, "F"], [21, 3, 1, 1, "Y"], [22, 5, 1, 1, "Y"],
  ]},

  { id: "celebrate", name: "庆祝 / celebrate", anim: "彩点闪烁", rects: [
    [9, 9, 1, 1, "K"], [10, 8, 1, 1, "K"], [11, 9, 1, 1, "K"],
    [15, 9, 1, 1, "K"], [16, 8, 1, 1, "K"], [17, 9, 1, 1, "K"],
    [2, 2, 1, 1, "P"], [4, 5, 1, 1, "B"], [21, 2, 1, 1, "E"], [23, 5, 1, 1, "X"], [2, 7, 1, 1, "V"], [24, 8, 1, 1, "R"],
  ]},

  { id: "kiss", name: "亲亲 / kiss", anim: "爱心飞向屏幕外", rects: [
    [10, 9, 1, 2, "K"], [15, 10, 2, 1, "K"],
    [12, 11, 2, 1, "R"],
    [20, 4, 1, 1, "R"], [22, 4, 1, 1, "R"], [20, 5, 3, 1, "R"], [21, 6, 1, 1, "R"],
  ]},
];

// ===== 日常场景 / Scenes（浅色背景 #FAF8F2，按时段或事件轮播）=====
// SCENES 不参与 gen_avatar_v3.js 的 40 帧打包，是给上层"日常小剧场"轮播用的素材库。
// SCENES are NOT part of the 40-frame sheet built by gen_avatar_v3.js — they are an extra
// library for a higher-level "ambient scene" rotation. trigger 只是建议的触发时机。
const SCENES = [
  { id: "meds", name: "吃药提醒 / medication reminder", trigger: "每天固定时间，确认前一直显示", rects: [
    ...EYES_DEFAULT,
    [20, 4, 5, 6, "G"], [21, 5, 3, 4, "W"], [21, 3, 3, 1, "F"],   // 药瓶 / pill bottle
    [1, 11, 1, 1, "R"], [1, 13, 1, 1, "X"], [1, 15, 1, 1, "B"],   // 三粒药丸 / three pills
  ]},

  { id: "breakfast", name: "早餐 / breakfast", trigger: "早间", rects: [
    ...EYES_DEFAULT,
    [1, 13, 5, 1, "G"], [2, 11, 3, 2, "C"], [3, 11, 1, 1, "X"],
    [23, 10, 2, 3, "S"],
  ]},

  { id: "work", name: "工作陪伴 / desk work", trigger: "工作日白天默认", rects: [
    ...EYES_DEFAULT,
    [20, 6, 5, 4, "G"], [21, 7, 3, 2, "W"], [19, 10, 6, 1, "d"],
  ]},

  { id: "coffee", name: "咖啡休息 / coffee break", trigger: "下午茶时段", rects: [
    [9, 9, 1, 1, "K"], [10, 8, 1, 1, "K"], [11, 9, 1, 1, "K"],
    [15, 9, 1, 1, "K"], [16, 8, 1, 1, "K"], [17, 9, 1, 1, "K"],
    [20, 9, 3, 3, "S"], [23, 10, 1, 1, "S"], [20, 9, 3, 1, "N"],
    [21, 5, 1, 1, "G"], [22, 7, 1, 1, "G"],
  ]},

  { id: "game", name: "打游戏 / gaming", trigger: "游戏时段", rects: [
    [9, 9, 2, 2, "K"], [15, 9, 2, 2, "K"],
    [9, 18, 8, 2, "d"], [10, 18, 1, 1, "R"], [15, 18, 1, 1, "B"],   // 手柄 / gamepad
    [2, 3, 1, 1, "V"], [23, 3, 1, 1, "V"], [4, 1, 1, 1, "X"], [21, 1, 1, 1, "X"],
  ]},

  { id: "workout", name: "运动 / workout", trigger: "运动打卡时段", rects: [
    [9, 10, 1, 1, "K"], [10, 9, 1, 1, "K"], [16, 9, 1, 1, "K"], [17, 10, 1, 1, "K"],
    [7, 3, 12, 1, "G"], [5, 2, 2, 3, "d"], [19, 2, 2, 3, "d"],      // 杠铃 / barbell
    [22, 6, 1, 2, "B"],                                             // 汗滴 / sweat
  ]},

  { id: "tv", name: "看电视 / watching TV", trigger: "晚间休闲", rects: [
    ...EYES_DEFAULT,
    [19, 6, 6, 5, "D"], [20, 7, 4, 3, "S"], [21, 11, 2, 1, "d"],    // 电视 / TV set
    [1, 12, 3, 2, "S"], [2, 11, 1, 1, "C"], [1, 11, 1, 1, "C"], [3, 11, 1, 1, "C"],  // 零食 / snacks
  ]},

  { id: "bath", name: "洗澡 / bath time", trigger: "洗漱时段", rects: [
    ...EYES_DEFAULT,
    [2, 12, 1, 5, "G"], [23, 12, 1, 5, "G"], [2, 16, 22, 1, "G"],
    [3, 12, 20, 4, "W"], [3, 12, 20, 1, "S"],
    [5, 10, 1, 1, "S"], [20, 9, 1, 1, "S"], [22, 11, 1, 1, "S"],    // 泡泡 / bubbles
    [17, 5, 2, 2, "X"], [18, 4, 1, 1, "X"], [16, 6, 1, 1, "F"],     // 小黄鸭 / rubber duck
  ]},

  { id: "walk", name: "散步 / going out", trigger: "傍晚外出", rects: [
    ...EYES_DEFAULT,
    [1, 8, 3, 2, "E"], [1, 7, 3, 1, "G"], [2, 10, 1, 2, "G"],       // 小书包 / backpack
    [4, 2, 2, 1, "S"], [3, 2, 1, 1, "W"], [20, 3, 3, 1, "S"], [22, 3, 1, 1, "W"],  // 云 / clouds
    [23, 1, 2, 2, "X"],                                             // 太阳 / sun
  ]},

  { id: "bored", name: "无聊 / bored", trigger: "长时间无互动", rects: [
    [9, 10, 2, 1, "K"], [15, 10, 2, 1, "K"],                        // 半眯眼 / half-lidded eyes
    [11, 12, 4, 1, "K"],                                            // 撇嘴 / flat mouth
    [21, 3, 1, 1, "K"], [23, 5, 1, 1, "K"], [21, 7, 1, 1, "K"],     // 苍蝇轨迹 / fly trail
    [22, 5, 1, 1, "d"],                                             // 苍蝇 / fly
  ]},

  { id: "away", name: "暂时离开 / away", trigger: "离开座位时", rects: [
    ...EYES_DEFAULT,
    [19, 3, 6, 5, "W"], [19, 3, 6, 1, "d"], [20, 4, 4, 3, "S"],     // 门牌 / door sign
    [21, 5, 2, 1, "O"],                                             // 牌子上露出的小钳子 / claw peeking out
    [1, 9, 1, 3, "G"],
  ]},

  { id: "meal", name: "吃饭 / meal", trigger: "午饭/晚饭时段", rects: [
    [9, 9, 1, 1, "K"], [10, 8, 1, 1, "K"], [11, 9, 1, 1, "K"],
    [15, 9, 1, 1, "K"], [16, 8, 1, 1, "K"], [17, 9, 1, 1, "K"],
    [1, 12, 6, 3, "W"], [1, 12, 6, 1, "S"],                         // 大碗 / bowl
    [2, 10, 1, 2, "C"], [4, 10, 1, 2, "C"], [3, 9, 1, 2, "C"],      // 热气 / steam
    [23, 11, 1, 3, "G"], [22, 11, 1, 1, "G"],                       // 筷子 / chopsticks
  ]},

  { id: "night", name: "该睡了 / bedtime", trigger: "深夜", lampDim: true, noBase: true, rects: [
    [10, 0, 1, 1, "M"], [14, 0, 1, 1, "M"], [11, 1, 3, 2, "M"], [12, 3, 1, 4, "G"],  // 灯调暗但不熄 / lamp dimmed, never off
    [2, 4, 2, 3, "X"],                                  // 月亮形抱枕 / moon-shaped cushion
    [4, 10, 4, 3, "W"],                                 // 枕头 / pillow
    [6, 7, 14, 7, "O"],                                 // 身体 / body
    [9, 10, 2, 1, "K"], [14, 10, 2, 1, "K"],            // 闭眼 / closed eyes
    [8, 12, 16, 1, "W"], [8, 13, 16, 3, "S"],           // 被子 / quilt
    [3, 16, 21, 1, "N"],                                // 床框 / bed frame
    [20, 3, 2, 1, "b"], [21, 4, 1, 1, "b"], [20, 5, 2, 1, "b"],  // z
  ]},
];

// ===== 元数据 / Metadata =====
const META = {
  grid: [26, 22],          // 逻辑网格 / logical grid
  screen: [320, 240],      // 固件全屏尺寸 / firmware full-screen size
  scale: 10,               // 固件放大倍数 / firmware scale factor
  offset: [30, 10],        // 固件居中偏移 / firmware centering offset
  bgExpression: "#111111", // 表情背景色 / expression background
  bgScene: "#FAF8F2",      // 场景背景色 / scene background
  note: "头顶小灯永远亮着；睡眠态换成 M 色调暗，但不熄灭。The head lamp is always lit; in sleep states it dims to M instead of turning off.",
};

if (typeof module !== "undefined") module.exports = { PALETTE, BASE, EXPRESSIONS, SCENES, META };
