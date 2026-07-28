#include "sim_display.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <SDL.h>
#include <SDL_syswm.h>
#ifdef __APPLE__
#include <objc/objc.h>
#include <objc/message.h>
/* NSFloatingWindowLevel = CGWindowLevelForKey(kCGFloatingWindowLevelKey) = 3 */
#define NS_FLOATING_WINDOW_LEVEL 3
#define NS_NORMAL_WINDOW_LEVEL   0
#endif

/* Border width in native pixels (scaled with window) */
#define LED_BORDER_PX 4

/* Native display aspect ratio (including border) */
#define NATIVE_W (SIM_LCD_H_RES + LED_BORDER_PX * 2)
#define NATIVE_H (SIM_LCD_V_RES + LED_BORDER_PX * 2)

/* Minimum window size as a fraction of native — easy to tweak. Aspect is free,
 * so MIN_W/MIN_H are just independent floors (lower = can shrink smaller). */
#define MIN_SCALE 0.25f
#define MIN_W ((int)(NATIVE_W * MIN_SCALE))
#define MIN_H ((int)(NATIVE_H * MIN_SCALE))

/* Default window geometry — the desktop strip docked in a corner. Tweak to
 * change how big / where Clawd opens (pos = top-left in screen points). */
#define STRIP_W 448
#define STRIP_H 77
#define STRIP_X 1466
#define STRIP_Y 998

/* Max dynamic LVGL width (a very wide strip) — bounds framebuffer/texture/buffers. */
#define MAX_LVGL_W 1536
/* Floor for the dynamic LVGL width, so a very tall window can't collapse it. */
#define MIN_LVGL_W 60

/* Framebuffer — always maintained, both modes read from this. Sized for the
 * widest dynamic resolution; only the first s_lvgl_w columns of each of the
 * SIM_LCD_V_RES rows are used (packed, row stride = s_lvgl_w). */
static uint16_t s_framebuffer[MAX_LVGL_W * SIM_LCD_V_RES];

/* Current LVGL logical width. The height is ALWAYS SIM_LCD_V_RES (172): we vary
 * only the width with the window's aspect ratio, so the SDL blit scales
 * uniformly on both axes (Clawd never stretches) while the scene's sky/grass
 * (lv_pct widths) reflow to fill. On firmware this whole mechanism is absent
 * (the panel is a fixed 320x172). */
static int s_lvgl_w = SIM_LCD_H_RES;
static lv_display_t *s_disp = NULL;

/* Mode flag */
static bool s_headless = false;
static bool s_quit = false;
static bool s_hidden = false;
static bool s_pinned = false;

/* Simulated tick for headless mode */
static uint32_t s_sim_tick = 0;

/* SDL state (interactive mode) */
static SDL_Window   *s_window   = NULL;
static SDL_Renderer *s_renderer = NULL;
static SDL_Texture  *s_texture  = NULL;
static int s_scale = 3;

/* RGB LED color (updated by led_strip shim via sim_rgb_led_update) */
static uint8_t s_led_r = 0, s_led_g = 0, s_led_b = 0;

/* ---- RGB LED bridge (called from led_strip shim) ---- */

void sim_rgb_led_update(uint8_t r, uint8_t g, uint8_t b)
{
    s_led_r = r;
    s_led_g = g;
    s_led_b = b;
}

/* ---- Simulated time ---- */

uint32_t sim_get_tick(void)
{
    return s_sim_tick;
}

void sim_advance_tick(uint32_t ms)
{
    s_sim_tick += ms;
}

static uint32_t sdl_tick_cb(void)
{
    return SDL_GetTicks();
}

/* ---- Hit-test callback for borderless window dragging + resizing ---- */
#define RESIZE_GRIP 8  /* pixels from edge that count as resize grip */

static SDL_HitTestResult hit_test_cb(SDL_Window *win, const SDL_Point *area, void *data)
{
    (void)data;
    int w, h;
    SDL_GetWindowSize(win, &w, &h);

    bool left   = area->x < RESIZE_GRIP;
    bool right  = area->x >= w - RESIZE_GRIP;
    bool top    = area->y < RESIZE_GRIP;
    bool bottom = area->y >= h - RESIZE_GRIP;

    if (top && left)     return SDL_HITTEST_RESIZE_TOPLEFT;
    if (top && right)    return SDL_HITTEST_RESIZE_TOPRIGHT;
    if (bottom && left)  return SDL_HITTEST_RESIZE_BOTTOMLEFT;
    if (bottom && right) return SDL_HITTEST_RESIZE_BOTTOMRIGHT;
    if (left)            return SDL_HITTEST_RESIZE_LEFT;
    if (right)           return SDL_HITTEST_RESIZE_RIGHT;
    if (top)             return SDL_HITTEST_RESIZE_TOP;
    if (bottom)          return SDL_HITTEST_RESIZE_BOTTOM;

    return SDL_HITTEST_DRAGGABLE;
}

/* ---- LVGL flush callback ---- */

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    int x1 = area->x1;
    int y1 = area->y1;
    int w  = area->x2 - area->x1 + 1;
    int h  = area->y2 - area->y1 + 1;

    uint16_t *src = (uint16_t *)px_map;
    for (int y = 0; y < h; y++) {
        memcpy(&s_framebuffer[(y1 + y) * s_lvgl_w + x1],
               &src[y * w],
               w * sizeof(uint16_t));
    }

    lv_display_flush_ready(disp);
}

/* Rounded window corners — defined near apply_pinned below (Cocoa interop) */
static void apply_corner_radius(float radius);

/* ---- Init ---- */

lv_display_t *sim_display_init(bool headless, int scale, bool bordered, bool pinned)
{
    s_headless = headless;
    s_pinned = pinned;
    s_scale = scale > 0 ? scale : 3;
    memset(s_framebuffer, 0, sizeof(s_framebuffer));

    /* Set LVGL tick source */
    if (headless) {
        lv_tick_set_cb(sim_get_tick);
    } else {
        /* Interactive: use SDL_GetTicks */
        SDL_SetHint(SDL_HINT_MAC_BACKGROUND_APP, "1");  /* Don't show in Dock */
        SDL_Init(SDL_INIT_VIDEO);
#ifdef __APPLE__
        /* Set activation policy to Accessory so the app hides from Dock
         * but still supports NSFloatingWindowLevel (always-on-top).
         * SDL_HINT_MAC_BACKGROUND_APP skips setActivationPolicy entirely,
         * which breaks window level management on macOS. */
        id nsapp = ((id(*)(id, SEL))objc_msgSend)((id)objc_getClass("NSApplication"),
                    sel_registerName("sharedApplication"));
        ((void(*)(id, SEL, long))objc_msgSend)(nsapp,
                    sel_registerName("setActivationPolicy:"), 1 /* NSApplicationActivationPolicyAccessory */);
#endif
        lv_tick_set_cb(sdl_tick_cb);

        /* Open at the fixed desktop-strip geometry (size + corner position). */
        int win_w = STRIP_W;
        int win_h = STRIP_H;

        Uint32 flags = SDL_WINDOW_RESIZABLE;
        if (!bordered) flags |= SDL_WINDOW_BORDERLESS;
        if (pinned) flags |= SDL_WINDOW_ALWAYS_ON_TOP;
        s_window = SDL_CreateWindow(
            "Clawd Tank Simulator",
            STRIP_X, STRIP_Y,
            win_w, win_h,
            flags);
        if (!s_window) {
            fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
            exit(1);
        }

        /* Minimum size = MIN_SCALE x native */
        SDL_SetWindowMinimumSize(s_window, MIN_W, MIN_H);

        if (!bordered) {
            SDL_SetWindowHitTest(s_window, hit_test_cb, NULL);
        }
        SDL_RaiseWindow(s_window);

        s_renderer = SDL_CreateRenderer(s_window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
        if (!s_renderer) {
            fprintf(stderr, "SDL_CreateRenderer failed: %s\n", SDL_GetError());
            exit(1);
        }

        /* Nearest-neighbor scaling for crisp pixels */
        SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "0");

        s_texture = SDL_CreateTexture(
            s_renderer,
            SDL_PIXELFORMAT_RGB565,
            SDL_TEXTUREACCESS_STREAMING,
            s_lvgl_w, SIM_LCD_V_RES);
        if (!s_texture) {
            fprintf(stderr, "SDL_CreateTexture failed: %s\n", SDL_GetError());
            exit(1);
        }

        /* Rounded window corners (macOS) — radius is easy to tweak */
        apply_corner_radius(14.0f);
    }

    /* Create LVGL display */
    lv_display_t *disp = lv_display_create(s_lvgl_w, SIM_LCD_V_RES);
    s_disp = disp;

    /* Allocate render buffers — sized for the widest dynamic resolution so a
     * wide strip still renders in a few partial chunks. */
    size_t buf_sz = MAX_LVGL_W * 20 * sizeof(uint16_t); /* 20-line partial buffer */
    void *buf1 = malloc(buf_sz);
    void *buf2 = malloc(buf_sz);

    lv_display_set_buffers(disp, buf1, buf2, buf_sz, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(disp, flush_cb);

    /* Match the LVGL width to the (non-native) strip aspect ratio at startup. */
    if (!headless) {
        sim_display_handle_resize();
    }

    return disp;
}

uint16_t *sim_display_get_framebuffer(void)
{
    return s_framebuffer;
}

/* ---- Tick ---- */

/* LED-glow border thickness in window px, scaled by the current window height
 * (height is the fixed axis: the LVGL panel is always SIM_LCD_V_RES tall). */
static int compute_border(int win_h)
{
    int b = (int)(LED_BORDER_PX * (float)win_h / NATIVE_H + 0.5f);
    return b < 1 ? 1 : b;
}

void sim_display_tick(void)
{
    if (s_headless || s_hidden) return;

    int win_w, win_h;
    SDL_GetWindowSize(s_window, &win_w, &win_h);

    int border = compute_border(win_h);

    /* Fill entire window with LED color (border glow) */
    SDL_SetRenderDrawColor(s_renderer, s_led_r, s_led_g, s_led_b, 255);
    SDL_RenderClear(s_renderer);

    /* Blit the framebuffer (s_lvgl_w x SIM_LCD_V_RES) inset by the border. Its
     * aspect ratio tracks the content area, so the scale is uniform on both
     * axes — the pixel-art never stretches. */
    SDL_UpdateTexture(s_texture, NULL, s_framebuffer, s_lvgl_w * sizeof(uint16_t));
    SDL_Rect dst = {
        .x = border,
        .y = border,
        .w = win_w - 2 * border,
        .h = win_h - 2 * border
    };
    SDL_RenderCopy(s_renderer, s_texture, NULL, &dst);

    SDL_RenderPresent(s_renderer);
}

bool sim_display_should_quit(void)
{
    return s_quit;
}

void sim_display_set_quit(void)
{
    s_quit = true;
}

/* ---- Dynamic resolution (free-aspect resize) ----
 *
 * The window resizes freely to ANY rectangle. We keep the LVGL logical height
 * fixed at SIM_LCD_V_RES and recompute the LVGL width to match the window's
 * content aspect ratio. Because the framebuffer aspect then equals the content
 * area aspect, the SDL blit scales uniformly on both axes: the pixel-art Clawd
 * stays in proportion (never stretched), while the scene's sky/grass (lv_pct
 * widths) reflow to fill the wider/narrower panel.
 *
 * Returns true if the LVGL resolution changed (caller should re-layout the UI). */
bool sim_display_handle_resize(void)
{
    if (!s_window || !s_disp) return false;

    int win_w, win_h;
    SDL_GetWindowSize(s_window, &win_w, &win_h);

    int border = compute_border(win_h);
    int cont_w = win_w - 2 * border;
    int cont_h = win_h - 2 * border;
    if (cont_w < 1) cont_w = 1;
    if (cont_h < 1) cont_h = 1;

    int new_w = (int)((float)SIM_LCD_V_RES * cont_w / cont_h + 0.5f);
    if (new_w < MIN_LVGL_W) new_w = MIN_LVGL_W;
    if (new_w > MAX_LVGL_W) new_w = MAX_LVGL_W;

    if (new_w == s_lvgl_w) return false;

    s_lvgl_w = new_w;
    lv_display_set_resolution(s_disp, s_lvgl_w, SIM_LCD_V_RES);

    /* Row stride changed → clear the framebuffer and force a full repaint. */
    memset(s_framebuffer, 0, sizeof(s_framebuffer));
    lv_obj_invalidate(lv_screen_active());

    /* Recreate the SDL texture at the new width. */
    if (s_texture) SDL_DestroyTexture(s_texture);
    s_texture = SDL_CreateTexture(
        s_renderer, SDL_PIXELFORMAT_RGB565, SDL_TEXTUREACCESS_STREAMING,
        s_lvgl_w, SIM_LCD_V_RES);

    return true;
}

/* Log current window geometry (size + on-screen position). Helps pick a good
 * fixed size/position for the desktop strip. */
void sim_display_log_geometry(const char *reason)
{
    if (!s_window) return;
    int w, h, x, y;
    SDL_GetWindowSize(s_window, &w, &h);
    SDL_GetWindowPosition(s_window, &x, &y);
    printf("[geom] %-6s size=%dx%d  pos=%d,%d\n", reason ? reason : "", w, h, x, y);
    fflush(stdout);
}

/* ---- Always-on-top ---- */

static void apply_pinned(void)
{
#ifdef __APPLE__
    /* Bypass SDL and set NSWindow level directly via Cocoa API.
     * SDL_SetWindowAlwaysOnTop doesn't work reliably with
     * SDL_HINT_MAC_BACKGROUND_APP on macOS. */
    SDL_SysWMinfo info;
    SDL_VERSION(&info.version);
    if (SDL_GetWindowWMInfo(s_window, &info) && info.subsystem == SDL_SYSWM_COCOA) {
        id nswindow = (id)info.info.cocoa.window;
        long level = s_pinned ? NS_FLOATING_WINDOW_LEVEL : NS_NORMAL_WINDOW_LEVEL;
        ((void(*)(id, SEL, long))objc_msgSend)(nswindow,
                    sel_registerName("setLevel:"), level);
    }
#else
    SDL_SetWindowAlwaysOnTop(s_window, s_pinned ? SDL_TRUE : SDL_FALSE);
#endif
}

/* ---- Cantos arredondados (macOS) ---- */
static void apply_corner_radius(float radius)
{
#ifdef __APPLE__
    SDL_SysWMinfo info;
    SDL_VERSION(&info.version);
    if (SDL_GetWindowWMInfo(s_window, &info) && info.subsystem == SDL_SYSWM_COCOA) {
        id win = (id)info.info.cocoa.window;

        ((void(*)(id, SEL, BOOL))objc_msgSend)(win, sel_registerName("setOpaque:"), NO);
        id clear = ((id(*)(id, SEL))objc_msgSend)((id)objc_getClass("NSColor"),
                    sel_registerName("clearColor"));
        ((void(*)(id, SEL, id))objc_msgSend)(win, sel_registerName("setBackgroundColor:"), clear);

        id view  = ((id(*)(id, SEL))objc_msgSend)(win, sel_registerName("contentView"));
        ((void(*)(id, SEL, BOOL))objc_msgSend)(view, sel_registerName("setWantsLayer:"), YES);
        id layer = ((id(*)(id, SEL))objc_msgSend)(view, sel_registerName("layer"));
        ((void(*)(id, SEL, double))objc_msgSend)(layer, sel_registerName("setCornerRadius:"), (double)radius);
        ((void(*)(id, SEL, BOOL))objc_msgSend)(layer, sel_registerName("setMasksToBounds:"), YES);
    }
#else
    (void)radius;
#endif
}

void sim_display_set_pinned(bool pinned)
{
    s_pinned = pinned;
    if (!s_window) return;
    apply_pinned();
}

/* ---- Show / Hide / Clear-quit ---- */

void sim_display_show_window(void)
{
    if (!s_window) return;
    SDL_ShowWindow(s_window);
    SDL_RaiseWindow(s_window);
    /* Re-apply always-on-top — macOS resets window level on show */
    if (s_pinned) {
        apply_pinned();
    }
    s_hidden = false;
}

void sim_display_hide_window(void)
{
    if (!s_window) return;
    SDL_HideWindow(s_window);
    s_hidden = true;
}

bool sim_display_is_hidden(void)
{
    return s_hidden;
}

void sim_display_clear_quit(void)
{
    s_quit = false;
}

/* ---- Shutdown ---- */

void sim_display_shutdown(void)
{
    if (!s_headless) {
        if (s_texture)  SDL_DestroyTexture(s_texture);
        if (s_renderer) SDL_DestroyRenderer(s_renderer);
        if (s_window)   SDL_DestroyWindow(s_window);
        SDL_Quit();
    }
}
