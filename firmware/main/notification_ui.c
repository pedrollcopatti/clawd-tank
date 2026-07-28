#include "notification_ui.h"
#include <string.h>
#include <stdio.h>

/* ---------- Floating banner ----------
 *
 * The notification UI is a compact card that floats over the bottom of the
 * scene instead of pushing Clawd aside. The scene keeps its full width and the
 * crab stays centered; this card just overlays the lower strip of the display.
 * It shows a single notification at a time (the newest), auto-rotating through
 * the rest when more than one is waiting. */

/* Banner geometry (recomputed for the current display size in layout_banner) */
#define BANNER_H        46   /* card height */
#define BANNER_MARGIN   6    /* left/right gap from screen edge */
#define BANNER_BOTTOM   6    /* gap from screen bottom */

/* Accent colors per notification slot (1:1 mapping, max 8) */
static const uint32_t accent_colors[NOTIF_MAX_COUNT] = {
    0xff6b2b, 0x4488ff, 0xaaaa33, 0x44aa44,
    0x7b68ee, 0x44cccc, 0xcc4488, 0xccaa22,
};

/* How long each notification stays featured before rotating to the next */
#define ROTATION_INTERVAL_MS 4000

/* ---------- Struct ---------- */

struct notification_ui_t {
    lv_obj_t *container;     /* the floating banner card itself */
    lv_obj_t *project_label;
    lv_obj_t *message_label;
    lv_obj_t *count_badge;   /* "i/N" indicator, shown only when N > 1 */

    /* Sorted notification cache */
    notification_t sorted[NOTIF_MAX_COUNT];
    int sorted_count;

    /* Auto-rotation */
    int featured_idx;     /* index into sorted[] currently featured */
    lv_timer_t *rotation_timer;
};

/* ---------- Forward declarations ---------- */

static void rotation_timer_cb(lv_timer_t *timer);
static void rebuild_display(notification_ui_t *ui);
static void layout_banner(notification_ui_t *ui);
static void fade_anim_cb(void *var, int32_t val);
static void fade_hide_completed_cb(lv_anim_t *a);

/* ---------- Layout ---------- */

/* Position/size the banner for the current display dimensions. On firmware the
 * display is a fixed 320x172; in the simulator it tracks the resizable window. */
static void layout_banner(notification_ui_t *ui)
{
    if (!ui) return;
    lv_display_t *disp = lv_display_get_default();
    int sw = lv_display_get_horizontal_resolution(disp);
    int sh = lv_display_get_vertical_resolution(disp);
    lv_obj_set_size(ui->container, sw - 2 * BANNER_MARGIN, BANNER_H);
    lv_obj_set_pos(ui->container, BANNER_MARGIN, sh - BANNER_H - BANNER_BOTTOM);
}

/* ---------- Create ---------- */

notification_ui_t *notification_ui_create(lv_obj_t *parent)
{
    notification_ui_t *ui = lv_malloc_zeroed(sizeof(notification_ui_t));
    if (!ui) return NULL;

    /* Container = the floating card. Solid rounded panel with accent border so
     * it reads as a distinct card sitting on top of the animated scene. */
    ui->container = lv_obj_create(parent);
    lv_obj_remove_style_all(ui->container);
    lv_obj_set_scrollbar_mode(ui->container, LV_SCROLLBAR_MODE_OFF);
    lv_obj_clear_flag(ui->container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(ui->container, LV_OBJ_FLAG_HIDDEN);

    lv_obj_set_style_bg_opa(ui->container, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(ui->container, lv_color_hex(0x2a1a3e), 0);
    lv_obj_set_style_border_width(ui->container, 2, 0);
    lv_obj_set_style_border_color(ui->container, lv_color_hex(accent_colors[0]), 0);
    lv_obj_set_style_radius(ui->container, 4, 0);
    lv_obj_set_style_pad_all(ui->container, 6, 0);
    layout_banner(ui);

    /* Project name — marquee scroll when text overflows. Leaves room on the
     * right for the count badge. */
    ui->project_label = lv_label_create(ui->container);
    lv_obj_set_style_text_font(ui->project_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(ui->project_label, lv_color_hex(0xffdd57), 0);
    lv_obj_set_pos(ui->project_label, 0, 0);
    lv_label_set_long_mode(ui->project_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(ui->project_label, lv_pct(80));

    /* Message — marquee scroll when text overflows */
    ui->message_label = lv_label_create(ui->container);
    lv_obj_set_style_text_font(ui->message_label, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(ui->message_label, lv_color_hex(0xcc99ff), 0);
    lv_obj_set_pos(ui->message_label, 0, 18);
    lv_label_set_long_mode(ui->message_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(ui->message_label, lv_pct(100));

    /* Count badge: "i/N", top-right corner, hidden when only one notification */
    ui->count_badge = lv_label_create(ui->container);
    lv_obj_set_style_text_font(ui->count_badge, &lv_font_montserrat_10, 0);
    lv_obj_set_style_text_color(ui->count_badge, lv_color_hex(0xffdd57), 0);
    lv_obj_align(ui->count_badge, LV_ALIGN_TOP_RIGHT, 0, 0);
    lv_label_set_text(ui->count_badge, "");

    /* Auto-rotation timer */
    ui->rotation_timer = lv_timer_create(rotation_timer_cb, ROTATION_INTERVAL_MS, ui);
    lv_timer_pause(ui->rotation_timer);

    ui->sorted_count = 0;
    ui->featured_idx = 0;

    return ui;
}

/* ---------- Show/Hide ---------- */

void notification_ui_show(notification_ui_t *ui, bool show, int anim_ms)
{
    if (!ui) return;

    if (show) {
        lv_obj_clear_flag(ui->container, LV_OBJ_FLAG_HIDDEN);
        if (ui->sorted_count > 1) {
            lv_timer_resume(ui->rotation_timer);
        }

        if (anim_ms > 0) {
            /* Fade in: transparent → opaque */
            lv_obj_set_style_opa(ui->container, LV_OPA_TRANSP, LV_PART_MAIN);
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, ui->container);
            lv_anim_set_values(&a, LV_OPA_TRANSP, LV_OPA_COVER);
            lv_anim_set_duration(&a, anim_ms);
            lv_anim_set_exec_cb(&a, fade_anim_cb);
            lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
            lv_anim_start(&a);
        } else {
            lv_obj_set_style_opa(ui->container, LV_OPA_COVER, LV_PART_MAIN);
        }
    } else {
        lv_timer_pause(ui->rotation_timer);

        if (anim_ms > 0) {
            /* Fade out: opaque → transparent, then hide */
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, ui->container);
            lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
            lv_anim_set_duration(&a, anim_ms);
            lv_anim_set_exec_cb(&a, fade_anim_cb);
            lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
            lv_anim_set_completed_cb(&a, fade_hide_completed_cb);
            lv_anim_start(&a);
        } else {
            lv_obj_set_style_opa(ui->container, LV_OPA_COVER, LV_PART_MAIN);
            lv_obj_add_flag(ui->container, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

/* ---------- Fade animation callbacks ---------- */

static void fade_anim_cb(void *var, int32_t val)
{
    lv_obj_set_style_opa((lv_obj_t *)var, (lv_opa_t)val, LV_PART_MAIN);
}

static void fade_hide_completed_cb(lv_anim_t *a)
{
    lv_obj_t *obj = (lv_obj_t *)a->var;
    lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    /* Reset opacity so the next fade-in starts clean */
    lv_obj_set_style_opa(obj, LV_OPA_COVER, LV_PART_MAIN);
}

/* ---------- Position tracking ---------- */

/* Kept for API compatibility: the banner no longer tracks a scene edge, so the
 * x argument is ignored — we just re-flow the banner for the current display
 * size (needed when the simulator window is resized). */
void notification_ui_set_x(notification_ui_t *ui, int x_px)
{
    (void)x_px;
    layout_banner(ui);
}

/* ---------- Rebuild from store ---------- */

void notification_ui_rebuild(notification_ui_t *ui, const notification_store_t *store)
{
    if (!ui || !store) return;

    /* Collect active notifications */
    ui->sorted_count = 0;
    for (int i = 0; i < NOTIF_MAX_COUNT; i++) {
        if (store->items[i].active) {
            ui->sorted[ui->sorted_count++] = store->items[i];
        }
    }

    /* Sort by seq ascending (insertion sort, max 8 elements) */
    for (int i = 1; i < ui->sorted_count; i++) {
        notification_t tmp = ui->sorted[i];
        int j = i - 1;
        while (j >= 0 && ui->sorted[j].seq > tmp.seq) {
            ui->sorted[j + 1] = ui->sorted[j];
            j--;
        }
        ui->sorted[j + 1] = tmp;
    }

    /* Featured = newest (highest seq = last in sorted array) */
    ui->featured_idx = ui->sorted_count > 0 ? ui->sorted_count - 1 : 0;

    /* Rotate only when there's more than one waiting */
    if (ui->sorted_count > 1) {
        lv_timer_reset(ui->rotation_timer);
        lv_timer_resume(ui->rotation_timer);
    } else {
        lv_timer_pause(ui->rotation_timer);
    }

    rebuild_display(ui);
}

/* ---------- New-notification emphasis ---------- */

/* Feature the newest notification immediately and restart the rotation clock
 * so it gets a full dwell. (Named for API compatibility — there is no longer a
 * separate expanded "hero" layout, the banner is always compact.) */
void notification_ui_trigger_hero(notification_ui_t *ui)
{
    if (!ui || ui->sorted_count == 0) return;
    ui->featured_idx = ui->sorted_count - 1;
    if (ui->sorted_count > 1) {
        lv_timer_reset(ui->rotation_timer);
        lv_timer_resume(ui->rotation_timer);
    }
    rebuild_display(ui);
}

/* ---------- Internal: rebuild LVGL widgets ---------- */

static void rebuild_display(notification_ui_t *ui)
{
    int count = ui->sorted_count;

    if (count == 0) {
        lv_label_set_text(ui->project_label, "");
        lv_label_set_text(ui->message_label, "");
        lv_label_set_text(ui->count_badge, "");
        return;
    }

    int fi = ui->featured_idx;
    if (fi >= count) fi = count - 1;

    int color_idx = fi % NOTIF_MAX_COUNT;
    lv_obj_set_style_border_color(ui->container,
                                  lv_color_hex(accent_colors[color_idx]), 0);

    lv_label_set_text(ui->project_label, ui->sorted[fi].project);
    lv_label_set_text(ui->message_label, ui->sorted[fi].message);

    if (count > 1) {
        char badge_buf[16];
        snprintf(badge_buf, sizeof(badge_buf), "%d/%d", fi + 1, count);
        lv_label_set_text(ui->count_badge, badge_buf);
    } else {
        lv_label_set_text(ui->count_badge, "");
    }
    lv_obj_align(ui->count_badge, LV_ALIGN_TOP_RIGHT, 0, 0);
}

/* ---------- Auto-rotation timer ---------- */

static void rotation_timer_cb(lv_timer_t *timer)
{
    notification_ui_t *ui = (notification_ui_t *)lv_timer_get_user_data(timer);
    if (!ui || ui->sorted_count <= 1) return;

    /* Cycle through notifications */
    ui->featured_idx = (ui->featured_idx + 1) % ui->sorted_count;
    rebuild_display(ui);
}
