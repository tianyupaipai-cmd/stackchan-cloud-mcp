# 像素表情包生成器 / Pixel Avatar Set Generator

给 StackChan（M5Stack CoreS3）用的表情素材工具链：用纯 JavaScript 把一组"填充矩形"画成
**40 帧 RGB565 位图**，拼成设备能直接加载的表情包。

A tiny toolchain that turns a list of fill-rects into a **40-frame RGB565 sprite sheet**
for a StackChan robot on an M5Stack CoreS3.

- **零依赖 / no dependencies** — 只用 Node.js 内置的 `fs` 和 `Buffer`，不需要 `npm install`
- **无二进制资产 / no binary assets in git** — 表情是代码，`avatar_set.bin` 由你本地生成
- **可读可改 / hackable** — 每个表情就是几行 `[x, y, w, h, 颜色]`

| 文件 / File | 作用 / Purpose |
| --- | --- |
| `sprites.js` | 调色板 `PALETTE`、基础身体 `BASE`、第一批表情 `EXPRESSIONS`、场景 `SCENES`、元数据 `META` |
| `new_expressions.js` | 第二批 9 个表情，每个带 A/B 两帧动画 |
| `gen_avatar_v3.js` | 主脚本：把上面两份素材拼成 `avatar_set.bin` |

---

## 怎么用 / Usage

```bash
node gen_avatar_v3.js
# → /path/to/sprites/avatar_set.bin 1536000 OK (40 frames)
```

然后通过 MCP 把它推给设备：

```
load_avatar_set(mode="layered", <avatar_set.bin>)
```

推送成功后，用 `set_avatar("happy")` 之类切换表情；说话时的口型由网关驱动
`set_mouth` / `set_mouth_sequence` 自动切换嘴型帧。

Push the file with the `load_avatar_set` MCP tool (`mode="layered"`), then switch faces with
`set_avatar`. Mouth frames are driven automatically while the device speaks.

> `avatar_set.bin` 有 1.5 MB，**不进仓库**（见本目录 `.gitignore`）。每次改完素材重新跑一次脚本即可。
> The generated `.bin` is gitignored on purpose — regenerate it whenever you edit the sprites.

---

## 格式规范 / Binary format

### 整体 / Overall

| 项 / Item | 值 / Value |
| --- | --- |
| 单帧尺寸 / frame size | 160 × 120 像素 |
| 像素格式 / pixel format | RGB565，**little-endian**（低字节在前 / low byte first） |
| 单帧字节 / bytes per frame | 160 × 120 × 2 = **38,400** |
| 帧数 / frame count | **40** |
| 文件总长 / total size | 40 × 38,400 = **1,536,000** |
| 文件头 / header | 无 / none |
| 帧间填充 / padding | 无，逐帧首尾相接 / none, frames are back to back |

字节序换算 / conversion:

```js
v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);   // 16-bit RGB565
bytes = [v & 0xff, v >> 8];                          // little-endian
```

### 帧布局 / Frame layout

| 索引 / Index | 数量 / Count | 内容 / Contents |
| --- | --- | --- |
| 0 – 15 | 16 | 16 张脸的 **A 帧** / face frames, animation frame A |
| 16 – 18 | 3 | 眼睛：`open` / `half` / `closed` |
| 19 – 23 | 5 | 嘴型：`closed` / `half` / `open` / `E` / `U` |
| 24 – 39 | 16 | 16 张脸的 **B 帧**（动画第二帧）/ face frames, animation frame B |

前 24 帧（16 + 3 + 5）是上游网关认识的标准 `layered` 布局；后面 16 帧是本项目的扩展，
用于让每个表情有两帧循环动画。

The first 24 frames are the layout upstream expects; the trailing 16 are this project's
animation extension giving every face a two-frame loop.

### 脸的槽位顺序 / Face slot order

**顺序不能改。** 必须严格对应固件里的 `FaceNameToIndex`：

| 0 | 1 | 2 | 3 |
| --- | --- | --- | --- |
| `idle` | `happy` | `thinking` | `sad` |

| 4 | 5 | 6 | 7 |
| --- | --- | --- | --- |
| `surprised` | `embarrassed` | `kiss` | `cry` |

| 8 | 9 | 10 | 11 |
| --- | --- | --- | --- |
| `sleep` | `hug` | `smug` | `heart_eyes` |

| 12 | 13 | 14 | 15 |
| --- | --- | --- | --- |
| `mischief` | `angry` | `worry` | `puzzled` |

B 帧的顺序（索引 24–39）与 A 帧一一对应，即索引 `24 + n` 是槽位 `n` 的第二帧。

---

## ⚠ 重要经验 / Hard-won lesson

**嘴型帧（索引 19–23）必须自带眼睛。**

这五帧是**整脸重绘**，不是叠在当前画面上的局部贴图。如果只画嘴不画眼，设备一说话，
每次切嘴型都会把眼睛擦掉——看上去像突然瞎了一样，而且很难定位，因为静态表情全都正常。

眼睛的三帧（索引 16–18）同理：也要把整张脸画完整。

> The five mouth frames MUST include the eyes. They are full-face redraws, not overlays — a
> mouth-only frame erases the eyes on every mouth change while the device is speaking. The
> same applies to the three eye frames.

代码里对应的是 `gen_avatar_v3.js` 里这一段——注意每个嘴型都拼了 `...EYES`：

```js
const mClosed = B(EYES);
const mHalf   = B([...EYES, [12,12,2,1,"K"]]);
const mOpen   = B([...EYES, [11,12,4,2,"K"]]);
```

---

## 怎么自己改表情 / Customizing

### 坐标系 / Coordinate system

所有素材画在一个 **26 × 22 的逻辑网格**上，不是像素坐标。原点 `(0,0)` 在左上角，
x 向右、y 向下。

每个矩形是 `[x, y, w, h, colorKey]`：

```js
[9, 9, 2, 2, "K"]   // 从 (9,9) 起，宽 2 格、高 2 格，用 K（近黑）填充
```

生成表情包时逻辑格放大 5 倍并偏移 `(15, 5)`，落到 160×120 的画布上：

```
像素 x = 15 + 逻辑x * 5
像素 y =  5 + 逻辑y * 5
```

设备固件全屏绘制时用的是另一套尺度（320×240，scale 10，偏移 30/10），见 `sprites.js` 的 `META`。

版面参考 / layout reference：

| 部位 / Part | 逻辑坐标 / Logical range |
| --- | --- |
| 身体 body | x 6–19, y 7–16 |
| 眼睛 eyes | y 9–11 |
| 嘴 mouth | y 12–13 |
| 头顶小灯 lamp | x 11–13, y 0–6 |
| 左右钳 claws | x 4–5 / x 20–21, y 9–11 |

### 颜色 / Palette

`sprites.js` 里的 `PALETTE` 把**单字母色键**映射到 `#RRGGBB`。**大小写敏感**——`Y` 是灯的亮黄，
`y` 是灯的光晕；`B` 是水蓝，`b` 是深一档的蓝。

改颜色直接改十六进制值即可；加新颜色就加一个没被占用的字母键。

```js
const PALETTE = {
  O: "#DD7E57",  // 身体主橙 / body orange
  K: "#1A1210",  // 眼睛/嘴 / eyes + mouth
  // ...
};
```

### A/B 帧动画机制 / Two-frame animation

固件每约 500 ms 在 A 帧和 B 帧之间来回切换。所以"动画"其实就是**画两张只差一点点的图**：

| 手法 / Technique | 例子 / Example |
| --- | --- |
| 元素位移 | `sleep` 的 zzz 从 `(20,4)` 飘到 `(22,2)` |
| 整体平移 | `happy` 的 B 帧把 `BASE` 每个 rect 的 y 减 1，看起来在跳 |
| 高亮轮转 | `thinking` 的三个点，A 帧亮左边、B 帧亮右边 |
| 元素增删 | `cry` 的 B 帧把泪柱换成飞溅的水点 |
| 形变 | `heart_eyes` 的 B 帧整体上移一格 |

`new_expressions.js` 里每个表情就是一个 `{ id, name, anim, A: [...], B: [...] }`。
`anim` 只是给人看的说明，渲染器不读。

带 `lampDim: true` 的表情（比如 `sleep`）会把头顶小灯的 `Y`/`y` 自动换成暗色 `M`——
灯**调暗但不熄灭**，这是设计上的常驻标识。

### 加一个新表情 / Adding an expression

1. 在 `new_expressions.js` 的 `NEW` 数组里加一项，写好 `id` / `A` / `B`；
2. 在 `gen_avatar_v3.js` 里那个 id 列表中放到**正确的槽位位置**：

```js
["kiss","cry","sleep","hug","smug","heart_eyes","mischief","angry","worry"].forEach(...)
```

3. 如果要**换掉**某个槽位而不是新增，直接改对应的 id；
4. 如果要**增加总帧数**（超过 40），必须同时改固件的布局预期和网关的尺寸白名单——
   见下面的"注意事项"。

改完跑一次 `node gen_avatar_v3.js`，脚本会自检脸数和总字节数，不对会直接报错。

### `SCENES` 是什么

`sprites.js` 里的 `SCENES` **不参与** 40 帧打包。它是给上层"日常场景轮播"用的额外素材库
（吃饭、看电视、散步……），可以按时段或事件单独渲染。想用的话自己写渲染逻辑，
或者把某个场景挪进 `EXPRESSIONS` / `NEW` 占一个槽位。

`SCENES` are **not** part of the 40-frame sheet — they are an extra library for a
higher-level ambient-scene rotation.

---

## 注意事项 / Caveats

**40 帧的包默认会被上游网关拒收。** 上游对 `layered` 模式做的是**精确尺寸匹配**，只认
14 帧 = 537,600 字节；推 1,536,000 字节会返回：

```
size_mismatch: got=1536000 expected=537600 (mode=layered)
```

连带后果是设备退回固件内置的默认 emoji 脸，之后所有 `set_avatar` 调用全部无效。

解决办法是给网关打补丁，把"精确匹配"放开成"合法尺寸白名单"——
完整说明见 [`../patches/README.md`](../patches/README.md) 的**补丁 #2**。

> A 40-frame sheet is rejected by the stock upstream gateway, which hard-codes an exact size
> for `layered` mode. Apply patch #2 in `../patches/README.md` to relax the check into a
> valid-size whitelist.

其他 / Others：

- 网关放开的只是**尺寸门禁**，不会替你转换布局。帧数和顺序必须和固件实际期望的对得上。
- 尺寸校验过了但 `set_avatar` 仍然不生效 → 是**布局问题**（槽位顺序错了），不是补丁问题。
- 补丁改的是已安装的包目录，`uv tool upgrade` 之后会被覆盖，需要重新执行 `apply_patches.sh`。
