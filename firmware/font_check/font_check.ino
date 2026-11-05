#include <Adafruit_GFX.h>
#include <U8g2_for_Adafruit_GFX.h>
extern const uint8_t u8g2_font_unifont_t_japanese1[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese1");
extern const uint8_t u8g2_font_unifont_t_japanese2[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese2");
extern const uint8_t u8g2_font_unifont_t_japanese3[] U8G2_FONT_SECTION("u8g2_font_unifont_t_japanese3");
class DummyGfx : public Adafruit_GFX {
public: DummyGfx() : Adafruit_GFX(10,10) {}
  void drawPixel(int16_t, int16_t, uint16_t) override {} };
DummyGfx dummy;
U8G2_FOR_ADAFRUIT_GFX jp;
void setup() {
  Serial.begin(115200);
  for (uint32_t t = 0; t < 2000 && !Serial; t++) delay(1);
  jp.begin(dummy);
  const char* test = "←→↑↓⇆⇅別曲横縦今流している音楽";
  const uint8_t* fonts[3] = {u8g2_font_unifont_t_japanese1, u8g2_font_unifont_t_japanese2, u8g2_font_unifont_t_japanese3};
  unsigned int i = 0;
  while (test[i]) {
    uint32_t cp = 0; int len = 0;
    uint8_t b = test[i];
    if (b < 0x80) { cp = b; len = 1; }
    else if ((b & 0xE0) == 0xC0) { cp = b & 0x1F; len = 2; }
    else if ((b & 0xF0) == 0xE0) { cp = b & 0x0F; len = 3; }
    else { i++; continue; }
    for (int k = 1; k < len; k++) cp = (cp << 6) | (test[i + k] & 0x3F);
    i += len;
    int found = 0;
    for (int f = 0; f < 3; f++) {
      jp.setFont(fonts[f]);
      if (u8g2_IsGlyph(&jp.u8g2, (uint16_t)cp)) { found = f + 1; break; }
    }
    if (!found) {
      // print the missing char as UTF-8 bytes + codepoint
      Serial.print("MISSING U+"); Serial.print(cp, HEX);
      Serial.print(" '");
      for (int k = 0; k < len; k++) Serial.write(test[i - len + k]);
      Serial.println("'");
    }
  }
  Serial.println("DONE");
}
void loop() { delay(4000); setup(); }
