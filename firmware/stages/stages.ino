#include <Seeed_GFX.h>
#include "board/boards/XIAO_LCD_Board.h"
#include "driver/tft/Driver_JD9853A.h"
#include "panel/Panel_TFT.h"
#include "touch/Touch_AXS5106L.h"
#include <Wire.h>
Seeed_GFX display;
Seeed_Sprite canvas;
Touch_AXS5106L touch(-1, D7, Wire, 172, 320);
static constexpr int8_t RST = 38, BL = 37;
void stage(String s) { Serial.print("DBG:S:"); Serial.println(s); Serial.flush(); }
void setup() {
  Serial.begin(115200);
  for (uint32_t t = 0; t < 2500 && !Serial; t++) delay(5);
  stage("1-serial");
  if (!display.begin<Board_XIAO_1inch47_Touch_Display<RST, BL>, Config_Seeed_1inch47_Touch_JD9853A>()) { stage("2-begin-FAIL:" + String(display.lastResult().message)); }
  stage("2-begin-ok");
  pinMode(BL, OUTPUT); analogWrite(BL, 160);
  stage("3-bl-pwm");
  if (!display.attachTouch(touch, display.panel().driver().bus())) stage("4-touch-FAIL");
  stage("4-touch-ok");
  display.setRotation(1);
  stage("5-rot");
  void* p = canvas.createSprite(display, 320, 172);
  stage(p ? "6-sprite-ok" : "6-sprite-null");
  uint16_t* fb = (uint16_t*)canvas.getPointer();
  stage(fb ? "7-fb-ok" : "7-fb-null");
  canvas.fillScreen(0xFFFF);
  stage("8-fill-white");
  canvas.pushSprite(0, 0);
  stage("9-push-white");
  delay(1500);
  canvas.fillScreen(0x0000);
  canvas.pushSprite(0, 0);
  stage("10-done");
}
void loop() { stage("tick"); delay(2000); }
