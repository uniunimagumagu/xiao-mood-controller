/*
 * XIAO Mood Controller — handset firmware v2 (thumbnail-centric player UI)
 * Board: Seeed Studio XIAO nRF52840 Plus with 1.47" Touch Display
 *        LCD JD9853A 172x320 used landscape (320x172), touch AXS5106L 0x63,
 *        IMU LSM6DS3TR-C 0x6A
 *
 * Design: dark music player. Thumbnail left, info right.
 *         see docs/ui_preview.png for the reference pixel layout.
 *
 * Serial (XIAO -> PC): EVT:..., DBG:...
 * Serial (PC -> XIAO) text lines:
 *   UI:CAT\t<label>\t<idx>/<n>
 *   UI:ITEM\t<title>\t<channel>\t<pos>/<n>   (recommendation lane: pos <= 0)
 *   UI:STAT\t<text> / UI:PLAY\t<title>       (PCOFF switches link off)
 *   DIAG 1 / DIAG 0
 * binary thumbnail frame:
 *   0xFE <w:u16le> <h:u16le> <w*h*2 bytes RGB565 little-endian>
 *
 * Gestures:
 *   swipe left  -> EVT:LEFT   (previous item in the lane)
 *   swipe right -> EVT:RIGHT  (next item in the lane)
 *   swipe up    -> EVT:UP     (next category)
 *   swipe down  -> EVT:DOWN   (previous category)
 *   tap         -> EVT:TAP    (play the displayed video)
 *   hold 1.5s   -> EVT:CHAT   (toggle device-side ChatGPT screen)
 *   pickup      -> EVT:PICKUP (wake only)
 */

#include <Seeed_GFX.h>
#include "board/boards/XIAO_LCD_Board.h"
#include "driver/tft/Driver_JD9853A.h"
#include "panel/Panel_TFT.h"
#include "touch/Touch_AXS5106L.h"
#include <Wire.h>
// Seeed_GFX already defines GFXfont/GFXglyph (identical layout); skipping
// Adafruit's copy lets both libraries coexist in one translation unit.
#define _GFXFONT_H_
#include <Adafruit_GFX.h>
#include <U8g2_for_Adafruit_GFX.h>

extern const uint8_t u8g2_font_unifont_t_japanese1[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese1");
extern const uint8_t u8g2_font_unifont_t_japanese2[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese2");
extern const uint8_t u8g2_font_unifont_t_japanese3[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese3");

namespace cfg {
  const bool SWIPE_LEFT_MEANS_NEXT = true;
  const int  SWIPE_THRESH_PX       = 30;
  const int  TAP_MAX_DISP          = 60;   // a stroke moving under this = tap
  const uint32_t GEST_DEBOUNCE_MS  = 160;
  const uint32_t LONGPRESS_MS      = 1500; // ChatGPT mode toggle
  const int16_t SEEK_ZONE_W        = 160;   // landscape halves (320/2)
  const int32_t SEEK_PAIR_MAX_DISP = 90;    // 2nd tap drift budget for seek
  const uint32_t SEEK_PAIR_MS      = 450;   // two taps in this window = seek
  const uint32_t LONGPRESS_HINT_MS = 700;  // show waiting hint
  const uint32_t SLEEP_AFTER_MS    = 30000;
  const uint32_t PICKUP_STILL_MS   = 4000;
  const uint32_t PICKUP_MOTION_MS  = 500;
  const float    PICKUP_GYRO_DPS   = 40.0;
  const uint32_t STARTUP_IGNORE_MS = 3000;
  const int  REDRAW_COALESCE_MS    = 12;
}


// FAST direct framebuffer layer: nothing goes through the sprite per-pixel calls
// -> text/icons/pictures cost microseconds instead of ~65ms per glyph.
uint16_t* fb = nullptr;
const int FB_W = 320, FB_H = 172;
void fbSet(int x, int y, uint16_t c) {
  if (x < 0 || y < 0 || x >= FB_W || y >= FB_H) return;
  fb[y * FB_W + x] = c;
}
void fbFillRect(int x, int y, int w, int h, uint16_t c) {
  for (int j = y; j < y + h; j++) {
    if (j < 0 || j >= FB_H) continue;
    int x0 = max(0, x), x1 = min(FB_W, x + w);
    for (int i = x0; i < x1; i++) fb[j * FB_W + i] = c;
  }
}
void fbFillScreen(uint16_t c) {
  for (int i = 0; i < FB_W * FB_H; i++) fb[i] = c;
}
void fbLineH(int x, int y, int w, uint16_t c) { fbFillRect(x, y, w, 1, c); }
void fbLineV(int x, int y, int h, uint16_t c) { fbFillRect(x, y, 1, h, c); }

void fbCircleFill(int cx, int cy, int r, uint16_t c) {
  for (int j = -r; j <= r; j++) for (int i = -r; i <= r; i++)
    if (i * i + j * j <= r * r) fbSet(cx + i, cy + j, c);
}
void fbCircleOutline(int cx, int cy, int r, uint16_t c) {
  for (int j = -r; j <= r; j++) for (int i = -r; i <= r; i++)
    if (((i * i + j * j) <= r * r) && ((i * i + j * j) > (r - 1) * (r - 1) + 1)) fbSet(cx + i, cy + j, c);
}
void fbFillTri(int x1, int y1, int x2, int y2, int x3, int y3, uint16_t c) {
  int minX = min(x1, min(x2, x3)), maxX = max(x1, max(x2, x3));
  int minY = min(y1, min(y2, y3)), maxY = max(y1, max(y2, y3));
  for (int j = minY; j <= maxY; j++) for (int i = minX; i <= maxX; i++) {
    int d1 = (i - x1) * (y2 - y1) - (j - y1) * (x2 - x1);
    int d2 = (i - x2) * (y3 - y2) - (j - y2) * (x3 - x2);
    int d3 = (i - x3) * (y1 - y3) - (j - y3) * (x1 - x3);
    bool neg = (d1 < 0) || (d2 < 0) || (d3 < 0);
    bool pos = (d1 > 0) || (d2 > 0) || (d3 > 0);
    if (!(neg && pos)) fbSet(i, j, c);
  }
}
void iconTriRight(int16_t x, int16_t y, int16_t w, uint16_t c) {
  fbFillTri(x + w, y, x, y - w / 2, x, y + w / 2, c);
}
void iconTriLeft(int16_t x, int16_t y, int16_t w, uint16_t c) {
  fbFillTri(x, y, x + w, y - w / 2, x + w, y + w / 2, c);
}
void iconNote(int16_t x, int16_t y, uint16_t c) {
  fbCircleFill(x, y, 3, c);
  fbLineV(x + 3, y - 12, 13, c);
  fbLineH(x + 3, y - 12, 6, c);
}
void iconDot(int16_t x, int16_t y, uint16_t c) { fbCircleFill(x, y, 5, c); }
void iconRing(int16_t x, int16_t y, uint16_t c) { fbCircleOutline(x, y, 5, c); }

// ---------- palette (dark player look) ----------
constexpr uint16_t COL_BG       = 0x0821; // near-black navy
constexpr uint16_t COL_BG2      = 0x1031; // header gradient low
constexpr uint16_t COL_HEAD1    = 0x083F;
constexpr uint16_t COL_ACCENT   = 0xF81F; // purple (RGB565 0xF81F)
constexpr uint16_t COL_ACCENT2  = 0x5F5F; // soft blue
constexpr uint16_t COL_WHITE    = 0xFFFF;
constexpr uint16_t COL_GREY     = 0x8410;
constexpr uint16_t COL_GREEN    = 0x40FF;
constexpr uint16_t COL_ORANGE   = 0xFD40;

constexpr uint16_t COL_LINE     = 0x5300;

// ---------- layout ----------
constexpr int16_t HEAD_H   = 30;
constexpr int16_t RX       = 172;
constexpr int16_t SCREEN_TEXT_X = 6;  // status text x

// ---------- display / sprite / ink ----------
Seeed_GFX display;
static constexpr int8_t LCD_RST_PIN = 38;
static constexpr int8_t LCD_BL_PIN  = 37;
Seeed_Sprite canvas;
bool spriteOk = false;
Touch_AXS5106L touch(-1, D7, Wire, 172, 320);

class GfxAdapter : public Adafruit_GFX {
 public:
  Seeed_GFX* d = nullptr;
  Seeed_Sprite* s = nullptr;
  explicit GfxAdapter(Seeed_GFX* disp) : Adafruit_GFX(172, 320), d(disp) {}
  void drawPixel(int16_t x, int16_t y, uint16_t c) override {
    if (fb) fbSet(x, y, c); else if (s) s->drawPixel(x, y, c); else if (d) d->drawPixel(x, y, c); }
  void drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t c) override {
    if (fb) fbLineH(x, y, w, c); else if (s) s->drawFastHLine(x, y, w, c); else if (d) d->drawFastHLine(x, y, w, c); }
  void drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t c) override {
    if (fb) fbLineV(x, y, h, c); else if (s) s->drawFastVLine(x, y, h, c); else if (d) d->drawFastVLine(x, y, h, c); }
};
GfxAdapter gfxAdapter(&display);
U8G2_FOR_ADAFRUIT_GFX jp;

const uint8_t* const JP_FONTS[3] = {
  u8g2_font_unifont_t_japanese1,
  u8g2_font_unifont_t_japanese2,
  u8g2_font_unifont_t_japanese3,
};

// ---------- state ----------
struct UiState {
  String catLabel = " ";
  String catIdx = "-/-";
  String itemTitle = " ...";
  String itemChannel = "";
  String itemIdx = "";
  String status = "";
  String playing = "";
  bool link = false;
  bool recLane = false;
  bool hasSelection = false;
  String itemIdLastShown = "";
  String chatQ = "", chatA = "";           // ChatGPT mode I/O
  String playedId = "";   // last accepted id from PC
  // PREP: next/prev pool items + neighbor categories, sent by the PC ahead of
  // the gesture so the device can flip screens locally (no PC roundtrip lag).
  String prepNextIdx = "", prepNextTitle = "", prepNextChannel = "";
  String prepPrevIdx = "", prepPrevTitle = "", prepPrevChannel = "";
  String prepUpCat = "",  prepUpCatIdx = "",  prepUpTitle = "", prepUpChannel = "", prepUpItemIdx="";
  String prepDownCat = "", prepDownCatIdx = "", prepDownTitle = "", prepDownChannel = "", prepDownItemIdx="";
};
UiState ui;
volatile bool uiDirty = true;
bool asleep = false;
uint32_t dirtyAtMs = 0;
uint32_t lastActivityMs = 0;
uint32_t bootMs = 0;
bool chatMode = false;
bool diagMode = false;
bool imuOk = false;

// ---------- touch gesture ----------
bool touching = false;
bool strokeEmitted = false;
int32_t tStartX = 0, tStartY = 0;
int32_t tMaxDisp = 0;
uint32_t tStartMs = 0;
uint32_t lastGestureMs = 0;
bool longpressFired = false;

// ---------- pickup ----------
uint32_t stillSinceMs = 0, motionSinceMs = 0;
bool inMotion = false;

// ---------- helpers ----------
String sanitize(String s, int maxLen) {
  s.replace("\t", " "); s.replace("\r", " "); s.replace("\n", " "); s.trim();
  if ((int)s.length() > maxLen * 3) s = s.substring(0, maxLen * 3);
  return s.length() ? s : " ";
}

// draw UTF-8 text via u8g2 fonts with per-glyph fall-back (japanese1/2/3)
int drawJP(int x, int y, const String& text, uint16_t color, int maxPxW, int maxLines) {
  jp.setForegroundColor(color);
  int line = 0, cx = x;
  const int lineH = 17;
  const char* s = text.c_str();
  unsigned int i = 0;
  while (s[i] && line < maxLines) {
    uint32_t cp = 0; int len = 0;
    uint8_t b0 = s[i];
    if (b0 < 0x80) { cp = b0; len = 1; }
    else if ((b0 & 0xE0) == 0xC0) { cp = b0 & 0x1F; len = 2; }
    else if ((b0 & 0xF0) == 0xE0) { cp = b0 & 0x0F; len = 3; }
    else if ((b0 & 0xF8) == 0xF0) { cp = b0 & 0x07; len = 4; }
    else { i++; continue; }
    for (int k = 1; k < len && s[i + k]; k++) cp = (cp << 6) | (s[i + k] & 0x3F);
    i += len;
    if (cp == '\n' || cp == '\r') { cx = x; line++; continue; }
    int found = 0;
    for (int f = 0; f < 3; f++) {
      jp.setFont(JP_FONTS[f]);
      if (u8g2_IsGlyph(&jp.u8g2, (uint16_t)cp)) { found = f + 1; break; }
    }
    if (!found) cp = '?';
    int w = u8g2_GetGlyphWidth(&jp.u8g2, (uint16_t)cp);
    if (w <= 0) w = 8;
    if (cx + w > x + maxPxW) { cx = x; line++; if (line >= maxLines) break; }
    jp.drawGlyph(cx, y + line * lineH, (uint16_t)cp);
    cx += w;
  }
  return y + (line + 1) * lineH;
}



// ---------- screens ----------
void screenMusic() {
  uint32_t ts = millis();
  { if (fb) fbFillScreen(COL_BG); else canvas.fillScreen(COL_BG); }
  // thin accent top edge + header text only (no meaningless bands)
  fbFillRect(0, 0, 320, 3, COL_ACCENT);
  iconNote(10, 26, COL_ACCENT);
  drawJP(26, 27, sanitize(ui.catLabel, 14), COL_WHITE, 220, 1);
  drawJP(280, 24, sanitize(ui.catIdx, 8), COL_ACCENT2, 38, 1);
  if (ui.link) iconDot(310, 18, COL_GREEN); else iconRing(310, 18, COL_GREEN);

  // NOW: what is actually streaming; a fresh selection gets "Opening..."
  static uint32_t tGlyph = 0, tIcons = 0, tPush = 0;
  Serial.print("DBG:st1="); Serial.print(tGlyph); Serial.print(" st2="); Serial.print(tIcons);
  Serial.print(" st3="); Serial.println(millis() - ts);
  bool opening = (!ui.playing.length() || ui.itemIdLastShown != ui.playedId);
  bool showOpening = opening;

  uint32_t tt = millis();
  drawJP(10, 56, sanitize(ui.itemTitle, 28), COL_WHITE, 300, 2);
  tGlyph = millis() - tt; tt = millis();
  iconTriRight(12, 100, 9, COL_GREEN);
  drawJP(24, 102, sanitize(ui.itemChannel, 22), COL_ACCENT2, 140, 1);
  tIcons = millis() - tt; tt = millis();

  String idx = sanitize(ui.itemIdx, 8);
  int pos = 0, total = 0;
  int sl = idx.indexOf('/');
  if (sl > 0) { pos = idx.substring(0, sl).toInt(); total = idx.substring(sl + 1).toInt(); }
  if (pos > 0 && total > 0) {
    int shown = (total <= 12) ? total : 12;
    for (int i = 0; i < shown; i++) {
      uint16_t c = (i == pos - 1) ? COL_ACCENT : 0x4229;
      fbCircleFill(10 + i * 11, 126, 3, c);
    }
    if (total > 12) drawJP(140, 130, String(pos) + "/" + String(total), COL_GREY, 60, 1);
  }

  iconTriRight(12, 152, 10, COL_GREEN);
  drawJP(26, 156, ui.hasSelection ? "タップで再生" : "Disabled", COL_GREY, 100, 1);
  if (showOpening) drawJP(RX + 70, 156, "Opening...", COL_ORANGE, 90, 1);
  else if (ui.playing.length()) drawJP(RX + 70, 156, "再生中", COL_GREEN, 60, 1);

  fbLineH(0, 160, 320, COL_LINE);
  String st = ui.status;
  if (!ui.link) st = "PC offline";
  if (st.length()) drawJP(SCREEN_TEXT_X, 172, sanitize(st, 24), 0xB597, 310, 1);
}

void screenChat() {
  { if (fb) fbFillScreen(COL_BG); else canvas.fillScreen(COL_BG); }
  drawJP(10, 20, "ChatGPT", COL_ACCENT, 100, 1);
  drawJP(90, 20, "PCのtmux画面で質問してください", COL_WHITE, 220, 1);
  fbLineH(0, 30, 320, COL_LINE);
  drawJP(10, 56, sanitize(ui.chatQ, 40), COL_WHITE, 300, 2);
  drawJP(10, 100, sanitize(ui.chatA, 60), 0xB597, 300, 4);
  drawJP(10, 164, "1.5秒長押しでMusicモードに戻る", COL_ACCENT2, 300, 1);
}

void drawScreen() {
  uint32_t t0 = millis();
  if (chatMode) screenChat();
  else screenMusic();
  uint32_t tPushStart = millis();
  if (spriteOk) canvas.pushSprite(0, 0);
  Serial.print("DBG:push="); Serial.println(millis() - tPushStart);
  uiDirty = false;
  uint16_t probe = 0;
  if (spriteOk) probe = canvas.readPixel(160, 90);
  static uint32_t drawCount = 0;
  drawCount++;
  Serial.print("DBG:draw#"); Serial.print(drawCount);
  Serial.print(" ms="); Serial.print(millis() - t0);
  Serial.print(" bl="); Serial.print(digitalRead(LCD_BL_PIN));
  Serial.print(" probe="); Serial.println(probe, HEX);
}

// frames of slide animation: full-screen blits of intermediate offsets
void pushSlide(int dx, int dy, int steps) {
  if (!spriteOk) return;
  for (int i = 1; i <= steps; i++) {
    canvas.pushSprite(dx * i, dy * i);
  }
  canvas.pushSprite(0, 0);
}

void wake() {
  if (asleep) { analogWrite(LCD_BL_PIN, 160); asleep = false; uiDirty = true; }
  lastActivityMs = millis();
}
void sleepNow() { if (!asleep) { asleep = true; analogWrite(LCD_BL_PIN, 0); } }

void emit(const char* ev) { Serial.print("EVT:"); Serial.println(ev); }

void gestureLog(const char* kind, int32_t dx, int32_t dy, uint32_t dt) {
  Serial.print("DBG:"); Serial.print(kind);
  Serial.print(" dx="); Serial.print(dx);
  Serial.print(" dy="); Serial.print(dy);
  Serial.print(" dt="); Serial.println(dt);
}

// ---------- IMU (pickup) ----------
constexpr uint8_t LSM_ADDR = 0x6A;
constexpr uint8_t REG_CTRL2_G = 0x11, REG_OUTX_L_G = 0x22, REG_WHO_AM_I = 0x0F;

bool imuInit() {
  Wire.beginTransmission(LSM_ADDR);
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(LSM_ADDR); Wire.write(REG_CTRL2_G); Wire.write(0x40); Wire.endTransmission();
  delay(20);
  return true;
}
float gyroMagnitude() {
  constexpr float G = 8.75f / 1000.0f;
  Wire.beginTransmission(LSM_ADDR); Wire.write(REG_OUTX_L_G); Wire.endTransmission(false);
  Wire.requestFrom((int)LSM_ADDR, 6);
  if (Wire.available() < 6) return 0;
  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.read();
  auto r16 = [&](int i) { return (int16_t)((b[i + 1] << 8) | b[i]); };
  float gx = r16(0) * G, gy = r16(2) * G, gz = r16(4) * G;
  return sqrtf(gx * gx + gy * gy + gz * gz);
}

// ---------- serial input: text lines + binary thumbnails ----------
char rxBuf[160];
uint8_t rxLen = 0;

void processLine(String line) {
  line.trim();
  if (line.startsWith("UI:CAT\t")) {
    int i1 = line.indexOf('\t', 7);
    ui.catLabel = (i1 > 7) ? line.substring(7, i1) : "";
    ui.catIdx = (i1 > 0) ? line.substring(i1 + 1) : "";
    ui.link = true; uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("UI:ITEM\t")) {
    int i1 = line.indexOf('\t', 8);
    int i2 = (i1 > 0) ? line.indexOf('\t', i1 + 1) : -1;
    String posTxt = (i2 > 0) ? line.substring(i2 + 1) : "";
    ui.itemTitle = (i1 > 8) ? line.substring(8, i1) : "";
    ui.itemChannel = (i1 > 0 && i2 > i1) ? line.substring(i1 + 1, i2) : "";
    ui.recLane = (posTxt.startsWith("0/") || posTxt == "-1" || posTxt == "-1/0");
    ui.itemIdx = posTxt;
    ui.hasSelection = true;
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("UI:NOW\t")) {
    int i1 = line.indexOf('\t', 7);
    int i2 = (i1 > 0) ? line.indexOf('\t', i1 + 1) : -1;
    int i3 = (i2 > 0) ? line.indexOf('\t', i2 + 1) : -1;
    ui.itemTitle = (i1 > 7) ? line.substring(7, i1) : "";
    ui.itemChannel = (i1 > 0 && i2 > i1) ? line.substring(i1 + 1, i2) : "";
    ui.itemIdx = (i2 > 0) ? line.substring(i2 + 1) : "";
    ui.itemIdLastShown = "";
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("UI:PLAYED\t")) {
    ui.playedId = line.substring(10);
    uiDirty = true; dirtyAtMs = millis();
  } else if (line.startsWith("UI:STAT\t")) {
    ui.status = line.substring(8);
    if (ui.status == "PCOFF") ui.link = false;
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("UI:PLAY\t")) {
    ui.playing = line.substring(8);
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("CHAT:Q\t")) {
    ui.chatQ = line.substring(7);
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line.startsWith("CHAT:A\t")) {
    ui.chatA = line.substring(7);
    uiDirty = true; dirtyAtMs = millis(); wake();
  } else if (line == "DIAG 1") { diagMode = true; Serial.println("DBG:diag on"); }
  else if (line == "DIAG 0") { diagMode = false; Serial.println("DBG:diag off"); }
}

void handleIncoming() {
  while (Serial.available()) {
    uint8_t c = (uint8_t)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLen > 0) { rxBuf[rxLen] = 0; processLine(String(rxBuf)); rxLen = 0; }
      continue;
    }
    if (rxLen < sizeof(rxBuf) - 1) rxBuf[rxLen++] = (char)c;
  }
}

// ---------- touch gestures (+ long-press) ----------
void handleTouch() {
  int32_t x = 0, y = 0;
  bool down = display.getTouch(&x, &y);

  static uint8_t missCount = 0;
  static int8_t pendZone = 0;             // 1 = left edge, 2 = right edge
  static uint32_t pendAt = 0;
  if (touching && !down) {
    if (++missCount < 3) return;
    touching = false;
    uint32_t now = millis();
    longpressFired = false;
    int32_t dx = x - tStartX, dy = y - tStartY;
    uint32_t dt = now - tStartMs;
    int32_t finDisp = abs(dx) > abs(dy) ? abs(dx) : abs(dy);
    if (finDisp > tMaxDisp) tMaxDisp = finDisp;
    bool tap = !strokeEmitted && tMaxDisp < cfg::TAP_MAX_DISP && dt < cfg::LONGPRESS_MS;
    gestureLog(tap ? "strokeEnd(TAP)" : "strokeEnd(moved)", dx, dy, dt);
    if (tap) {
      wake();
      if (chatMode) { emit("TAP"); strokeEmitted = false; return; }  // chat: no play
      int8_t zone = 0;
      if (x < cfg::SEEK_ZONE_W) zone = 1;
      else if (x > 320 - cfg::SEEK_ZONE_W) zone = 2;
      if (zone != 0 && tMaxDisp >= cfg::SEEK_PAIR_MAX_DISP) return;  // borderline: ignore
      if (zone == 0) {
        emit("TAP");                               // center zone = play current
      } else if (pendZone == zone && (now - pendAt) <= cfg::SEEK_PAIR_MS) {
        // second tap of the pair -> ±60s seek (YouTube-style), no chatter
        emit(zone == 1 ? "SEEKBACK" : "SEEKFWD");
        gestureLog(zone == 1 ? "seek-back" : "seek-fwd", x, y, (now - pendAt));
        pendZone = 0;
      } else {
        pendZone = zone; pendAt = now;             // wait for the 2nd tap
        gestureLog("tapzone-pend", x, y, now - pendAt);
      }
    } else if (pendZone) {
      pendZone = 0;
    }
    strokeEmitted = false;
    return;
  }
  if (down) {
    missCount = 0;
    if (!touching) {
      touching = true; tStartX = x; tStartY = y; tStartMs = millis();
      strokeEmitted = false; tMaxDisp = 0; longpressFired = false;
      wake();
      return;
    }
    uint32_t dt = millis() - tStartMs;
    int32_t dx = x - tStartX, dy = y - tStartY;
    int32_t adx = abs(dx), ady = abs(dy);
    int32_t disp = adx > ady ? adx : ady;
    if (disp > tMaxDisp) tMaxDisp = disp;
    if (!strokeEmitted && disp < cfg::TAP_MAX_DISP && dt >= cfg::LONGPRESS_MS) {
      if (!longpressFired) {
        longpressFired = true;
        chatMode = !chatMode;
        if (chatMode) { ui.chatQ = ""; ui.chatA = ""; emit("CHAT"); }
        uiDirty = true; dirtyAtMs = millis();
      }
      return;
    }
    if (millis() - lastGestureMs < cfg::GEST_DEBOUNCE_MS) return;
    if (adx <= cfg::SWIPE_THRESH_PX && ady <= cfg::SWIPE_THRESH_PX) return;
    if (adx > ady) {
      bool forward = (dx < 0) ^ !cfg::SWIPE_LEFT_MEANS_NEXT;
      // local instant flip using the PC's pre-sent item (no roundtrip)
      const String& t = forward ? ui.prepNextTitle : ui.prepPrevTitle;
      if (t.length() && t != "-") {
        if (forward) { ui.itemTitle = ui.prepNextTitle; ui.itemChannel = ui.prepNextChannel; ui.itemIdx = ui.prepNextIdx; }
        else         { ui.itemTitle = ui.prepPrevTitle;  ui.itemChannel = ui.prepPrevChannel;  ui.itemIdx = ui.prepPrevIdx; }
        uiDirty = true; dirtyAtMs = 0;   // draw this loop, skip coalescing
      }
      emit(forward ? "RIGHT" : "LEFT");
      gestureLog("swipeH", dx, dy, dt);
    } else {
      bool nextCat = (dy < 0);
      const String& cl = nextCat ? ui.prepUpCat : ui.prepDownCat;
      if (cl.length() && cl != "-") {
        ui.catLabel = cl;
        ui.catIdx   = nextCat ? ui.prepUpCatIdx : ui.prepDownCatIdx;
        ui.itemTitle   = nextCat ? ui.prepUpTitle   : ui.prepDownTitle;
        ui.itemChannel = nextCat ? ui.prepUpChannel : ui.prepDownChannel;
        ui.itemIdx     = nextCat ? ui.prepUpItemIdx : ui.prepDownItemIdx;
        uiDirty = true; dirtyAtMs = 0;
      }
      emit(nextCat ? "UP" : "DOWN");
      gestureLog("swipeV", dx, dy, dt);
    }
    strokeEmitted = true;
    lastGestureMs = millis();
    tStartX = x; tStartY = y;
  }
}

void setup() {
  bootMs = millis();
  Serial.begin(115200);
  for (uint32_t t = 0; t < 1500 && !Serial; t++) delay(10);
  if (!display.begin<
          Board_XIAO_1inch47_Touch_Display<LCD_RST_PIN, LCD_BL_PIN>,
                     Config_Seeed_1inch47_Touch_JD9853A>()) {
    Serial.println(display.lastResult().message);
  }
  if (!display.attachTouch(touch, display.panel().driver().bus())) {
    Serial.println("DBG:touch attach failed");
  }
  // backlight driven with PWM like the official Seeed demo (BL=37)
  pinMode(LCD_BL_PIN, OUTPUT);
  analogWrite(LCD_BL_PIN, 160);
  imuOk = imuInit();
  display.setRotation(1);
  spriteOk = (canvas.createSprite(display, 320, 172) != nullptr);
  gfxAdapter.s = spriteOk ? &canvas : nullptr;
  fb = spriteOk ? (uint16_t*)canvas.getPointer() : nullptr;
  if (!spriteOk || !fb) Serial.println("DBG:fb unavailable (slow path)");
  jp.begin(gfxAdapter);
  drawScreen();
  lastActivityMs = millis();
  Serial.println("DBG:handset ready (player UI)");
}

void loop() {
  handleIncoming();
  handleTouch();

  if (diagMode) {
    static uint32_t last = 0;
    if (millis() - last >= 300) {
      last = millis();
      Serial.print("DBG:gyro=");
      Serial.println(gyroMagnitude(), 1);
    }
  }
  if (uiDirty && !asleep && millis() - dirtyAtMs >= cfg::REDRAW_COALESCE_MS) {
    drawScreen();
  }
  delay(2);
}
