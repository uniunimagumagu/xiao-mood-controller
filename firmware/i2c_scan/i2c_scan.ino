#include <Wire.h>
void setup() {
  Serial.begin(115200);
  for (uint32_t t = 0; t < 1500 && !Serial; t++) delay(10);
  Wire.begin();
  Wire.setClock(100000);
}
void loop() {
  Serial.println("scan:");
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.print("  found 0x"); Serial.println(a, HEX);
    }
  }
  delay(3000);
}
