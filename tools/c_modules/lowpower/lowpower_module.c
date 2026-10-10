// lowpower: ESP32-S3 light sleep with wake on any GPIO level (T-Deck v1 + Pro).
//
// MicroPython's machine.lightsleep() only knows RTC-pin wakes (ext0/ext1)
// and clears every wake source before it sleeps. The v1's LoRa IRQ (GPIO 45)
// and keyboard interrupt (GPIO 46) are not RTC pins, so both boards use
// ESP-IDF's digital GPIO wake instead, which works on any pin in light sleep.
//
//   lowpower.sleep(ms, pins) -> WAKE_TIMER | WAKE_GPIO | WAKE_OTHER
//       pins: tuple of (gpio, wake_level, restore_trigger[, ...])
//   lowpower.usb_connected() -> bool     (a USB host is sending start-of-frame)
//
// The app's console is TinyUSB CDC on the USB OTG controller, not the
// USB-serial-JTAG peripheral (that one only serves the ROM loader at PID
// 0x1001; bench 2026-10-09: usb_serial_jtag_is_connected() read False with
// mpremote live). TinyUSB's own tud_mounted() cannot be referenced from here:
// the link then pulls ESP-IDF's managed TinyUSB in beside MicroPython's copy
// (duplicate dcd_int_handler). So the OTG core's frame counter is read
// directly: an attached, awake host sends a start-of-frame every 1 ms, so a
// counter that moves across 3 ms means a host is there. A charger, an
// unplugged cable or a host in suspend all leave it still -- and unlike a
// "configured" flag it cannot go stale after the cable is pulled.
//
// gpio_wakeup_enable() rewrites a pin's interrupt type to level-sensitive
// and never puts it back. DIO1 carries the LoRa driver's rising-edge ISR:
// left level-sensitive it would re-enter that ISR for as long as DIO1 is
// high, and the Python that clears the radio over SPI could never run. So
// each pin's interrupt is disabled before arming and its trigger restored
// after -- also on the error paths, before anything is raised.

#include "py/runtime.h"
#include "py/mphal.h"
#include "esp_sleep.h"
#include "esp_err.h"
#include "driver/gpio.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "soc/usb_dwc_struct.h"

#define LP_MAX_PINS 8
#define LP_WAKE_TIMER 0
#define LP_WAKE_GPIO 1
#define LP_WAKE_OTHER 2

static bool rtc_periph_kept = false;

static void lp_restore(size_t n, const int *gpio, const int *restore) {
    for (size_t i = 0; i < n; i++) {
        gpio_wakeup_disable(gpio[i]);
        gpio_set_intr_type(gpio[i], (gpio_int_type_t)restore[i]);
        if (restore[i] != GPIO_INTR_DISABLE) {
            // The GPIO block keeps latching edges while the interrupt is
            // disabled, and a level wake itself leaves the status set. Clear
            // it before re-enabling, or the pin's ISR fires at once on every
            // wake: on the v1 the trackball's falling-edge ISRs then counted
            // phantom nudges and the screen lit for no one (bench
            // 2026-10-10, "wakes up but no new message").
            if (gpio[i] < 32) {
                gpio_ll_clear_intr_status(&GPIO, 1u << gpio[i]);
            } else {
                gpio_ll_clear_intr_status_high(&GPIO, 1u << (gpio[i] - 32));
            }
            gpio_intr_enable(gpio[i]);
        }
    }
    esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
}

static mp_obj_t lp_sleep(mp_obj_t ms_in, mp_obj_t pins_in) {
    mp_int_t ms = mp_obj_get_int(ms_in);
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(pins_in, &n, &items);
    if (n > LP_MAX_PINS) {
        mp_raise_ValueError(MP_ERROR_TEXT("too many wake pins"));
    }
    int gpio[LP_MAX_PINS], level[LP_MAX_PINS], restore[LP_MAX_PINS];
    for (size_t i = 0; i < n; i++) {
        size_t m;
        mp_obj_t *t;
        mp_obj_get_array(items[i], &m, &t);
        if (m < 3) {
            mp_raise_ValueError(MP_ERROR_TEXT("wake pin needs (gpio, level, restore)"));
        }
        gpio[i] = mp_obj_get_int(t[0]);
        level[i] = mp_obj_get_int(t[1]) ? 1 : 0;
        restore[i] = mp_obj_get_int(t[2]);
        if (!GPIO_IS_VALID_GPIO(gpio[i])) {
            mp_raise_ValueError(MP_ERROR_TEXT("bad gpio"));
        }
        if (restore[i] < GPIO_INTR_DISABLE || restore[i] >= GPIO_INTR_MAX) {
            mp_raise_ValueError(MP_ERROR_TEXT("bad restore trigger"));
        }
    }

    esp_err_t err = ESP_OK;
    size_t armed = 0;
    for (size_t i = 0; i < n && err == ESP_OK; i++) {
        gpio_intr_disable(gpio[i]);
        err = gpio_wakeup_enable(gpio[i], level[i] ? GPIO_INTR_HIGH_LEVEL : GPIO_INTR_LOW_LEVEL);
        armed = i + 1;
    }
    if (err == ESP_OK && n > 0) {
        err = esp_sleep_enable_gpio_wakeup();
    }
    if (err == ESP_OK && ms > 0) {
        err = esp_sleep_enable_timer_wakeup((uint64_t)ms * 1000);
    }
    if (err == ESP_OK && !rtc_periph_kept) {
        // Keep the RTC peripheral domain powered. With no RTC wake source
        // armed ESP-IDF switches it off in light sleep, and every RTC-domain
        // pad (GPIO 0-21) loses its drive and pull-ups: on the T-Deck v1 that
        // is GPIO 10, the peripheral power switch, so the display, keyboard
        // and radio browned out on each sleep (bench 2026-10-09, the whole
        // deck flashing), and the floating trackball lines woke the chip at
        // once. Meshtastic's light sleep does the same. The setting is
        // reference-counted and global (it also applies to any deep sleep),
        // so it is taken exactly once for the life of the firmware.
        err = esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
        rtc_periph_kept = (err == ESP_OK);
        // ESP-IDF (CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND, on in both builds)
        // configures EVERY pad to be isolated in light sleep -- input, output
        // and pulls off -- and switches to that configuration automatically;
        // only wake pins are exempt. On the T-Deck v1 the trackball lines
        // then lose their pull-ups and float: a falling edge latches during
        // the slice and fires as a "nudge" on wake, lighting the screen for
        // nobody (bench 2026-10-10, /rns/wake.txt said "ball:down"); the
        // keyboard's I2C lines float the same way. Keep every pad's awake
        // configuration through sleep instead.
        esp_sleep_enable_gpio_switch(false);
    }
    if (err != ESP_OK) {
        lp_restore(armed, gpio, restore);
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("lowpower arm: %d"), (int)err);
    }

    esp_err_t slept = esp_light_sleep_start();
    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    lp_restore(n, gpio, restore);

    if (slept == ESP_ERR_SLEEP_REJECT) {
        // A wake line was already active when we asked: that is a wake, the
        // caller classifies it by reading the pins.
        return MP_OBJ_NEW_SMALL_INT(LP_WAKE_GPIO);
    }
    if (slept != ESP_OK) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("lowpower sleep: %d"), (int)slept);
    }
    int r = LP_WAKE_OTHER;
    if (cause == ESP_SLEEP_WAKEUP_TIMER) {
        r = LP_WAKE_TIMER;
    } else if (cause == ESP_SLEEP_WAKEUP_GPIO) {
        r = LP_WAKE_GPIO;
    }
    return MP_OBJ_NEW_SMALL_INT(r);
}
static MP_DEFINE_CONST_FUN_OBJ_2(lp_sleep_obj, lp_sleep);

static mp_obj_t lp_usb_connected(void) {
    uint32_t a = USB_DWC.dsts_reg.soffn;
    mp_hal_delay_ms(3);
    uint32_t b = USB_DWC.dsts_reg.soffn;
    return mp_obj_new_bool(a != b);
}
static MP_DEFINE_CONST_FUN_OBJ_0(lp_usb_connected_obj, lp_usb_connected);

static const mp_rom_map_elem_t lowpower_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_lowpower) },
    { MP_ROM_QSTR(MP_QSTR_sleep), MP_ROM_PTR(&lp_sleep_obj) },
    { MP_ROM_QSTR(MP_QSTR_usb_connected), MP_ROM_PTR(&lp_usb_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_WAKE_TIMER), MP_ROM_INT(LP_WAKE_TIMER) },
    { MP_ROM_QSTR(MP_QSTR_WAKE_GPIO), MP_ROM_INT(LP_WAKE_GPIO) },
    { MP_ROM_QSTR(MP_QSTR_WAKE_OTHER), MP_ROM_INT(LP_WAKE_OTHER) },
};
static MP_DEFINE_CONST_DICT(lowpower_globals, lowpower_globals_table);

const mp_obj_module_t lowpower_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&lowpower_globals,
};
MP_REGISTER_MODULE(MP_QSTR_lowpower, lowpower_module);
