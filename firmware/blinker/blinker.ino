void setup() {
  Serial.begin(115200);
  for (uint32_t t = 0; t < 3000 && !Serial; t++) delay(10);
  Serial.println("DBG:blinker alive");
}
void loop() {
  Serial.println("DBG:tick");
  delay(1000);
}
