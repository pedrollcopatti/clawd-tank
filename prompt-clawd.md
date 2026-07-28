# Ajustes na janela do simulador (clawd-tank)

No arquivo `simulator/sim_display.c`, faça dois ajustes na janela do simulador
(macOS, SDL2). Preserve o comportamento atual: arrastar a janela sem borda, o
always-on-top e a proporção fixa da tela.

## 1) Permitir encolher a janela além do mínimo atual

Hoje o tamanho mínimo está travado em 1× do nativo (`NATIVE_W` × `NATIVE_H`), em
dois lugares: na chamada `SDL_SetWindowMinimumSize` dentro de `sim_display_init`,
e no clamp dentro de `sim_display_enforce_aspect_ratio` (que reseta para
`NATIVE_W`/`NATIVE_H` quando o tamanho fica menor).

- Logo após as definições de `NATIVE_W`/`NATIVE_H`, adicione um fator de escala
  mínima configurável:

      #define MIN_SCALE 0.4f
      #define MIN_W ((int)(NATIVE_W * MIN_SCALE))
      #define MIN_H ((int)(NATIVE_H * MIN_SCALE))

- Use `MIN_W`/`MIN_H` na chamada de `SDL_SetWindowMinimumSize`.
- No clamp de `sim_display_enforce_aspect_ratio`, troque os limites
  `NATIVE_W`/`NATIVE_H` por `MIN_W`/`MIN_H`, mantendo a proporção (quando estourar
  o mínimo, setar `new_w = MIN_W` e `new_h = MIN_H`).

## 2) Arredondar os cantos da janela (macOS)

Reaproveite o mesmo padrão de interop com o Cocoa via `objc_msgSend` que já é
usado na função `apply_pinned`. Adicione esta função perto dela:

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

Chame `apply_corner_radius(14.0f);` uma única vez no fim da criação da janela em
`sim_display_init`, logo depois do bloco que cria `s_texture`. Deixe o raio
(`14.0f`) e o `MIN_SCALE` como valores fáceis de ajustar.

## Compilar e verificar

Por fim, compile com:

    cd simulator && cmake --build build

e confirme que builda sem erros. Se o recorte dos cantos não pegar no conteúdo
renderizado pelo SDL, aplique também `setMasksToBounds:YES` na layer da subview
de render do SDL.
