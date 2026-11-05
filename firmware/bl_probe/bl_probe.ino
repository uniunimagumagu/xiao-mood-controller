// Probe every GPIO: if a display backlight is wired to any pin, the panel will glow.
// Watch the board; the serial log shows which pin is currently HIGH.
const uint8_t pins[] = {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,17,18,19};
void setup() { Serial.begin(115200); for (uint32_t t=0;t<1500&&!Serial;t++) delay(10); }
void loop() {
  for (uint8_t p : pins) {
    pinMode(p, OUTPUT);
    Serial.print("pin D"); Serial.println(p);
    digitalWrite(p, HIGH); delay(700);
    digitalWrite(p, LOW);  delay(300);
    pinMode(p, INPUT);     // leave floating
  }
  Serial.println("--- round done ---");
  delay(2000);
}
