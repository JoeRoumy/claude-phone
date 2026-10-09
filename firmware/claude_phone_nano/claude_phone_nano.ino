// Claude Phone firmware for the LGT8F328P Nano: the whole phone on one USB cable.
//
// Samples the handset mic and streams it to the Mac, plays earpiece cues on the built-in DAC, reads the
// hook switch and keypad, and drives the electronic ringer. Protocol (250000 baud), matching
// mac/claude_phone/phone.py:
//   Nano -> Mac:  text lines   READY | HOOK_UP | HOOK_DOWN | KEY <0-9 * #> | PONG | ERR <reason>
//                 audio frames 0xFF, n, then n 8-bit mu-law samples at 16 kHz (sent while the handset is
//                              lifted). Text and samples never contain 0xFF, so a 0xFF always starts a frame.
//   Mac -> Nano:  RING <on_ms>[,<off_ms>,<on_ms>...] | STOP | BEEP | TONE <hz> <ms> | MIC <0|1> | PING
//
// Bench test without a phone: a jumper from D2 to GND = handset lifted; a jumper from D4 (DAC) to A0
// (mic in) loops earpiece tones back into the mic. The onboard LED shows the hook state and flashes
// with the ring.

// --- Pins (adjust once the phone's keypad and ringer are investigated) -------------------------------

const uint8_t HOOK_PIN = 2;
const uint8_t HOOK_LIFTED_LEVEL = LOW;  // hook contact closes to GND when lifted; use HIGH if yours opens

const uint8_t MIC_CHANNEL = 0;  // A0
// Earpiece: DAC0 on D4 (LGT8F328P built-in 8-bit DAC)

const uint8_t ROW_PINS[4] = {3, 5, 6, 7};
const uint8_t COL_PINS[3] = {8, 10, 12};
const char KEYMAP[4][3] = {
  {'1', '2', '3'},
  {'4', '5', '6'},
  {'7', '8', '9'},
  {'*', '0', '#'},
};

const uint8_t RING_PIN = 9;
const uint8_t LED_PIN = LED_BUILTIN;

// --- Timing ------------------------------------------------------------------------------------------

const unsigned long BAUD = 250000;
const unsigned int SAMPLE_RATE = 16000;
const uint8_t FRAME_SAMPLES = 64;
const uint8_t FRAME_START = 0xFF;

const unsigned long HOOK_DEBOUNCE_MS = 30;
const unsigned long KEY_DEBOUNCE_MS = 20;
const unsigned long KEY_SCAN_MS = 5;
const unsigned long WARBLE_MS = 31;  // switch tone about 16 times a second, like an electronic ringer
const unsigned int WARBLE_LOW_HZ = 500;
const unsigned int WARBLE_HIGH_HZ = 600;
const unsigned int MAX_SEGMENT_MS = 10000;
const uint8_t MAX_CADENCE = 16;
const unsigned int BEEP_HZ = 1000;
const unsigned int BEEP_MS = 250;

// --- Audio: sampled in the Timer1 interrupt -------------------------------------------------------

// One cycle of a sine wave, centred on 128 (the DAC's midpoint), amplitude 100.
const uint8_t SINE[32] = {
  128, 148, 166, 184, 199, 211, 220, 226, 228, 226, 220, 211, 199, 184, 166, 148,
  128, 108,  90,  72,  57,  45,  36,  30,  28,  30,  36,  45,  57,  72,  90, 108,
};

volatile uint8_t audioBuffer[256];  // ring buffer; uint8_t indices wrap for free
volatile uint8_t audioHead = 0;     // written by the interrupt
volatile uint8_t audioTail = 0;     // read by loop()
volatile bool streaming = false;
volatile uint16_t overruns = 0;

volatile uint16_t tonePhase = 0;
volatile uint16_t toneStep = 0;
volatile uint32_t toneSamplesLeft = 0;

bool micForced = false;  // MIC 1: stream even with the handset down (testing)

// G.711 mu-law. Maps 0xFF (+0) to 0x7F (-0), the same value, so samples never collide with FRAME_START.
uint8_t muLaw(int16_t sample) {
  const int16_t CLIP = 32635;
  const int16_t BIAS = 0x84;
  uint8_t sign = 0;
  if (sample < 0) {
    sign = 0x80;
    sample = sample <= -CLIP ? CLIP : -sample;
  }
  if (sample > CLIP) sample = CLIP;
  sample += BIAS;
  uint8_t exponent = 7;
  for (uint16_t mask = 0x4000; !(sample & mask) && exponent > 0; mask >>= 1) exponent--;
  uint8_t mantissa = (sample >> (exponent + 3)) & 0x0F;
  uint8_t encoded = ~(sign | (exponent << 4) | mantissa);
  return encoded == 0xFF ? 0x7F : encoded;
}

ISR(TIMER1_COMPA_vect) {
  // The conversion started last time is done by now (it takes a few microseconds; this runs every 62.5).
  uint16_t raw = ADC;
  ADCSRA |= _BV(ADSC);

  if (streaming) {
    uint8_t next = audioHead + 1;
    if (next != audioTail) {
      audioBuffer[audioHead] = muLaw((int16_t)((int16_t)raw - 2048) << 4);
      audioHead = next;
    } else {
      overruns++;
    }
  }

  if (toneSamplesLeft) {
    tonePhase += toneStep;
    DAL0 = SINE[tonePhase >> 11];
    toneSamplesLeft--;
  } else {
    DAL0 = 128;
  }
}

void startAudio() {
  pinMode(DAC0, ANALOG);
  DAL0 = 128;
  analogReference(DEFAULT);
  analogRead(A0 + MIC_CHANNEL);  // lets the core set up ADMUX for the mic channel and reference

  noInterrupts();
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS10);  // CTC mode, no prescaler
  OCR1A = F_CPU / SAMPLE_RATE - 1;
  TIMSK1 = _BV(OCIE1A);
  interrupts();
  ADCSRA |= _BV(ADSC);
}

void playTone(unsigned int hz, unsigned int ms) {
  noInterrupts();
  tonePhase = 0;
  toneStep = (uint32_t)hz * 65536UL / SAMPLE_RATE;
  toneSamplesLeft = (uint32_t)ms * SAMPLE_RATE / 1000;
  interrupts();
}

void setStreaming(bool on) {
  if (on == streaming) return;
  noInterrupts();
  audioTail = audioHead;  // start each stream with an empty buffer
  streaming = on;
  interrupts();
}

// Sends one frame if there's a full one, or whatever is left when `all` is set.
void sendAudio(bool all) {
  uint8_t available = audioHead - audioTail;
  if (available == 0 || (!all && available < FRAME_SAMPLES)) return;
  uint8_t count = available < FRAME_SAMPLES ? available : FRAME_SAMPLES;
  uint8_t frame[FRAME_SAMPLES + 2];
  frame[0] = FRAME_START;
  frame[1] = count;
  for (uint8_t i = 0; i < count; i++) frame[i + 2] = audioBuffer[(uint8_t)(audioTail + i)];
  audioTail += count;
  Serial.write(frame, count + 2);
}

// --- Hook switch -------------------------------------------------------------------------------------

bool hookLifted = false;
bool hookRaw = false;
unsigned long hookRawSince = 0;

bool readHook() {
  return digitalRead(HOOK_PIN) == HOOK_LIFTED_LEVEL;
}

void updateHook(unsigned long now) {
  bool raw = readHook();
  if (raw != hookRaw) {
    hookRaw = raw;
    hookRawSince = now;
  }
  if (hookRaw != hookLifted && now - hookRawSince >= HOOK_DEBOUNCE_MS) {
    hookLifted = hookRaw;
    if (hookLifted) {
      Serial.println(F("HOOK_UP"));
    } else {
      while (audioHead != audioTail) sendAudio(true);  // the last of the recording goes before HOOK_DOWN
      Serial.println(F("HOOK_DOWN"));
    }
  }
}

// --- Keypad ------------------------------------------------------------------------------------------

char keyHeld = 0;        // key last reported (0 = none)
char keyCandidate = 0;   // key seen by the latest scan
unsigned long keyCandidateSince = 0;
unsigned long lastScan = 0;

char scanKeypad() {
  for (uint8_t r = 0; r < 4; r++) {
    pinMode(ROW_PINS[r], OUTPUT);  // drive one row low; the others float so two pressed keys can't short
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(10);
    for (uint8_t c = 0; c < 3; c++) {
      if (digitalRead(COL_PINS[c]) == LOW) {
        pinMode(ROW_PINS[r], INPUT);
        return KEYMAP[r][c];
      }
    }
    pinMode(ROW_PINS[r], INPUT);
  }
  return 0;
}

void updateKeypad(unsigned long now) {
  if (now - lastScan < KEY_SCAN_MS) return;
  lastScan = now;
  char key = scanKeypad();
  if (key != keyCandidate) {
    keyCandidate = key;
    keyCandidateSince = now;
  }
  if (keyCandidate != keyHeld && now - keyCandidateSince >= KEY_DEBOUNCE_MS) {
    keyHeld = keyCandidate;
    if (keyHeld) {
      Serial.print(F("KEY "));
      Serial.println(keyHeld);
    }
  }
}

// --- Ringer ------------------------------------------------------------------------------------------

unsigned int cadence[MAX_CADENCE];
uint8_t cadenceLength = 0;
uint8_t segment = 0;
unsigned long segmentStart = 0;
bool ringing = false;
bool warbleHigh = false;
unsigned long warbleSince = 0;

void stopRing() {
  ringing = false;
  noTone(RING_PIN);
  digitalWrite(RING_PIN, LOW);
}

void startSegment(unsigned long now) {
  segmentStart = now;
  if (segment % 2 == 0) {  // even segments ring, odd segments are pauses
    warbleHigh = false;
    warbleSince = now;
    tone(RING_PIN, WARBLE_LOW_HZ);
  } else {
    noTone(RING_PIN);
  }
}

void updateRing(unsigned long now) {
  if (!ringing) return;
  if (now - segmentStart >= cadence[segment]) {
    segment++;
    if (segment >= cadenceLength) {
      stopRing();
      return;
    }
    startSegment(now);
  }
  if (segment % 2 == 0 && now - warbleSince >= WARBLE_MS) {
    warbleHigh = !warbleHigh;
    warbleSince = now;
    tone(RING_PIN, warbleHigh ? WARBLE_HIGH_HZ : WARBLE_LOW_HZ);
  }
}

// Parses "350" or "300,200,300" into the cadence; returns false if malformed.
bool parseCadence(const char *text) {
  uint8_t count = 0;
  while (*text) {
    if (count >= MAX_CADENCE || *text < '0' || *text > '9') return false;
    unsigned long ms = 0;
    while (*text >= '0' && *text <= '9') {
      ms = ms * 10 + (*text - '0');
      if (ms > MAX_SEGMENT_MS) return false;
      text++;
    }
    cadence[count++] = ms;
    if (*text == ',') text++;
    else if (*text) return false;
  }
  cadenceLength = count;
  return count > 0;
}

// --- Serial commands ---------------------------------------------------------------------------------

char line[64];
uint8_t lineLength = 0;

void handleCommand(char *command) {
  for (char *p = command; *p; p++) *p = toupper(*p);
  if (strncmp(command, "RING ", 5) == 0) {
    if (parseCadence(command + 5)) {
      ringing = true;
      segment = 0;
      startSegment(millis());
    } else {
      Serial.println(F("ERR bad ring cadence"));
    }
  } else if (strcmp(command, "STOP") == 0) {
    stopRing();
  } else if (strcmp(command, "BEEP") == 0) {
    playTone(BEEP_HZ, BEEP_MS);
  } else if (strncmp(command, "TONE ", 5) == 0) {
    unsigned int hz = 0, ms = 0;
    if (sscanf(command + 5, "%u %u", &hz, &ms) == 2 && hz > 0 && hz < SAMPLE_RATE / 2 && ms <= MAX_SEGMENT_MS) {
      playTone(hz, ms);
    } else {
      Serial.println(F("ERR bad tone"));
    }
  } else if (strcmp(command, "MIC 1") == 0 || strcmp(command, "MIC 0") == 0) {
    micForced = command[4] == '1';
  } else if (strcmp(command, "PING") == 0) {
    Serial.println(F("PONG"));
  } else if (command[0]) {
    Serial.println(F("ERR unknown command"));
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLength] = 0;
      handleCommand(line);
      lineLength = 0;
    } else if (lineLength < sizeof(line) - 1) {
      line[lineLength++] = c;
    }
  }
}

// -----------------------------------------------------------------------------------------------------

void setup() {
  Serial.begin(BAUD);
  pinMode(HOOK_PIN, INPUT_PULLUP);
  for (uint8_t r = 0; r < 4; r++) pinMode(ROW_PINS[r], INPUT);
  for (uint8_t c = 0; c < 3; c++) pinMode(COL_PINS[c], INPUT_PULLUP);
  pinMode(RING_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  startAudio();

  Serial.println(F("READY"));
  hookRaw = hookLifted = readHook();
  if (hookLifted) Serial.println(F("HOOK_UP"));
}

void loop() {
  unsigned long now = millis();
  readSerial();
  updateHook(now);
  updateKeypad(now);
  updateRing(now);
  setStreaming(hookLifted || micForced);
  sendAudio(false);
  // LED: flashes with the ring, otherwise on while the handset is lifted
  bool ringOn = ringing && segment % 2 == 0;
  digitalWrite(LED_PIN, ringOn ? ((now / 100) % 2) : hookLifted);
}
