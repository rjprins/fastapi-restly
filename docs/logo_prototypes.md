---
orphan: true
---

# `fr.` logo prototypes

:::{note}
This is the temporary review archive. It is intentionally absent from the documentation navigation; the selected 85 px version now powers the live documentation branding.
:::

Direction 01 and Archivo are selected for the FastAPI-Restly lockup at an 85 px title against the 88 px icon. The controls below retain the nearby comparisons.

<style>
  @import url("https://fonts.googleapis.com/css2?family=Archivo:wght@700&family=IBM+Plex+Sans:wght@600&family=Manrope:wght@700&display=swap");

  .wordmark-study {
    display: grid;
    gap: 0;
    margin: 1.5rem 0 3rem;
    border-bottom: 1px solid var(--pst-color-border);
  }

  .wordmark-option {
    padding: 1.5rem 0;
    border-top: 1px solid var(--pst-color-border);
  }

  .wordmark-option__label {
    margin: 0 0 1rem;
    color: var(--pst-color-text-muted);
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .wordmark-option__lockup {
    display: flex;
    align-items: center;
    gap: clamp(0.75rem, 2vw, 1.25rem);
    min-width: 0;
    color: var(--pst-color-text-base);
  }

  .wordmark-option__lockup img {
    width: clamp(3.75rem, 10vw, 5.5rem);
    height: auto;
    flex: none;
  }

  .wordmark-option__name {
    font-size: clamp(1.75rem, 5vw, 3.25rem);
    line-height: 1;
    letter-spacing: -0.045em;
    white-space: nowrap;
  }

  .wordmark-option__nav {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    margin: 1.25rem 0 0;
    color: var(--pst-color-text-base);
  }

  .wordmark-option__nav img {
    width: 2rem;
    height: 2rem;
  }

  .wordmark-option__nav span {
    font-size: 1.125rem;
    line-height: 1;
    letter-spacing: -0.025em;
  }

  .wordmark-manrope {
    font-family: "Manrope", sans-serif;
    font-weight: 700;
  }

  .wordmark-archivo {
    font-family: "Archivo", sans-serif;
    font-weight: 700;
  }

  .wordmark-option--selected {
    --archivo-title-size: 85px;
    --archivo-title-fluid-size: 5.902778vw;
    --archivo-title-min-size: 28.011px;
    --archivo-nav-title-size: 30.909px;
  }

  .wordmark-option--selected .wordmark-option__lockup {
    gap: clamp(6.591px, 1.388889vw, 20px);
  }

  .wordmark-option--selected .wordmark-option__lockup img {
    width: clamp(29px, 6.111111vw, 88px);
  }

  .wordmark-option--selected .wordmark-option__name {
    font-size: clamp(
      var(--archivo-title-min-size),
      var(--archivo-title-fluid-size),
      var(--archivo-title-size)
    );
  }

  .wordmark-option--selected .wordmark-option__nav {
    gap: 7.273px;
    font-size: var(--archivo-nav-title-size);
  }

  .wordmark-option--selected .wordmark-option__nav img {
    width: 32px;
    height: 32px;
  }

  .wordmark-option--selected .wordmark-option__nav span {
    font-size: 1em;
  }

  .wordmark-option--selected .wordmark-option__label {
    color: var(--pst-color-primary);
  }

  .wordmark-size-control {
    display: grid;
    grid-template-columns: auto minmax(12rem, 1fr) auto;
    gap: 0.75rem 1rem;
    align-items: center;
    padding: 1rem;
    border: 1px solid var(--pst-color-border);
    background: var(--pst-color-surface);
  }

  .wordmark-size-control label {
    margin: 0;
    font-weight: 700;
  }

  .wordmark-size-control input {
    width: 100%;
    accent-color: var(--pst-color-primary);
  }

  .wordmark-size-control output {
    min-width: 12rem;
    color: var(--pst-color-text-muted);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .wordmark-size-control p {
    grid-column: 1 / -1;
    margin: 0;
    color: var(--pst-color-text-muted);
    font-size: 0.875rem;
  }

  .wordmark-size-presets {
    display: flex;
    grid-column: 1 / -1;
    gap: 0.5rem;
    align-items: center;
  }

  .wordmark-size-presets button {
    padding: 0.35rem 0.7rem;
    border: 1px solid var(--pst-color-border);
    border-radius: 0.2rem;
    background: var(--pst-color-surface);
    color: var(--pst-color-text-base);
    font: inherit;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }

  .wordmark-size-presets button:hover {
    border-color: var(--pst-color-primary);
  }

  .wordmark-size-presets button[aria-pressed="true"] {
    border-color: var(--pst-color-primary);
    color: var(--pst-color-primary);
    font-weight: 700;
  }

  .wordmark-size-presets button:focus-visible {
    outline: 2px solid var(--pst-color-primary);
    outline-offset: 2px;
  }

  .wordmark-plex {
    font-family: "IBM Plex Sans", sans-serif;
    font-weight: 600;
  }

  .logo-scale-test {
    display: flex;
    align-items: end;
    gap: 2rem;
    margin-top: 1rem;
  }

  .logo-scale-test figure {
    display: grid;
    gap: 0.5rem;
    justify-items: center;
    margin: 0;
  }

  .logo-scale-test figcaption {
    color: var(--pst-color-text-muted);
    font-size: 0.75rem;
  }

  @media (max-width: 30rem) {
    .wordmark-option__name {
      font-size: 1.5rem;
    }

    .wordmark-size-control {
      grid-template-columns: 1fr auto;
    }

    .wordmark-size-control input {
      grid-column: 1 / -1;
      grid-row: 2;
    }

    .wordmark-size-control output {
      min-width: auto;
    }
  }
</style>

## Selected wordmark and alternatives

The 88 px icon stays fixed while you tune the Archivo title beside it. Once chosen, that title/icon ratio scales as a unit.

<div class="wordmark-size-control">
  <label for="archivo-wordmark-size">Title size against 88 px icon</label>
  <input id="archivo-wordmark-size" type="range" min="44" max="110" step="1" value="85">
  <output id="archivo-wordmark-size-output" for="archivo-wordmark-size" aria-live="polite">85 px title · 88 px icon</output>
  <div class="wordmark-size-presets" role="group" aria-label="Title size presets">
    <button type="button" data-title-size="85" aria-pressed="true">85 px</button>
    <button type="button" data-title-size="90" aria-pressed="false">90 px</button>
    <button type="button" data-title-size="95" aria-pressed="false">95 px</button>
  </div>
  <p>Choose the ratio at design scale. The display and navigation previews then preserve exactly the same proportions.</p>
</div>

<div class="wordmark-study">
  <section class="wordmark-option wordmark-option--selected">
    <p class="wordmark-option__label">Selected — Archivo 700 · 85 px title / 88 px icon</p>
    <div class="wordmark-option__lockup wordmark-archivo">
      <img src="_images/01-editorial.svg" alt="">
      <span class="wordmark-option__name">FastAPI-Restly</span>
    </div>
    <div class="wordmark-option__nav wordmark-archivo" aria-label="Selected Archivo lockup at documentation navigation size">
      <img src="_images/01-editorial.svg" alt="">
      <span>FastAPI-Restly</span>
    </div>
  </section>
  <section class="wordmark-option">
    <p class="wordmark-option__label">Alternative — Manrope 700</p>
    <div class="wordmark-option__lockup wordmark-manrope">
      <img src="_images/01-editorial.svg" alt="">
      <span class="wordmark-option__name">FastAPI-Restly</span>
    </div>
    <div class="wordmark-option__nav wordmark-manrope" aria-label="Manrope lockup at documentation navigation size">
      <img src="_images/01-editorial.svg" alt="">
      <span>FastAPI-Restly</span>
    </div>
  </section>
  <section class="wordmark-option">
    <p class="wordmark-option__label">Alternative — IBM Plex Sans 600</p>
    <div class="wordmark-option__lockup wordmark-plex">
      <img src="_images/01-editorial.svg" alt="">
      <span class="wordmark-option__name">FastAPI-Restly</span>
    </div>
    <div class="wordmark-option__nav wordmark-plex" aria-label="IBM Plex Sans lockup at documentation navigation size">
      <img src="_images/01-editorial.svg" alt="">
      <span>FastAPI-Restly</span>
    </div>
  </section>
</div>

<script>
  (() => {
    const slider = document.querySelector("#archivo-wordmark-size");
    const output = document.querySelector("#archivo-wordmark-size-output");
    const selected = document.querySelector(".wordmark-option--selected");
    const selectedLabel = selected?.querySelector(".wordmark-option__label");
    const presets = document.querySelectorAll("[data-title-size]");

    if (!slider || !output || !selected) {
      return;
    }

    const iconSize = 88;
    const navigationIconSize = 32;
    const minimumIconSize = 29;
    const designViewport = 1440;

    const updateWordmarkSize = () => {
      const titleSize = Number(slider.value);
      const navigationTitleSize = titleSize * navigationIconSize / iconSize;
      const fluidTitleSize = titleSize / (designViewport / 100);
      const minimumTitleSize = titleSize * minimumIconSize / iconSize;

      selected.style.setProperty("--archivo-title-size", `${titleSize}px`);
      selected.style.setProperty("--archivo-title-fluid-size", `${fluidTitleSize}vw`);
      selected.style.setProperty("--archivo-title-min-size", `${minimumTitleSize}px`);
      selected.style.setProperty("--archivo-nav-title-size", `${navigationTitleSize}px`);
      output.textContent = `${titleSize} px title · ${iconSize} px icon`;
      slider.setAttribute(
        "aria-valuetext",
        `${titleSize} pixel title against an ${iconSize} pixel icon`,
      );

      if (selectedLabel) {
        selectedLabel.textContent =
          `Selected — Archivo 700 · ${titleSize} px title / ${iconSize} px icon`;
      }

      presets.forEach((preset) => {
        preset.setAttribute(
          "aria-pressed",
          String(Number(preset.dataset.titleSize) === titleSize),
        );
      });
    };

    presets.forEach((preset) => {
      preset.addEventListener("click", () => {
        slider.value = preset.dataset.titleSize;
        updateWordmarkSize();
      });
    });

    slider.addEventListener("input", updateWordmarkSize);
    updateWordmarkSize();
  })();
</script>

## Selected icon — Editorial sans

:::::{grid} 1 2 2 2
:gutter: 3

::::{grid-item}
:::{image} ../design/logo-prototypes/01-editorial.svg
:alt: Editorial fr. monogram
:width: 280px
:align: center
:::
::::

::::{grid-item}
An unboxed, heavy lowercase mark. The open letterforms feel established and direct; the teal period carries the only brand colour.

{download}`Open the standalone SVG <../design/logo-prototypes/01-editorial.svg>`

### Scale test

<div class="logo-scale-test" aria-label="Editorial mark at several sizes">
  <figure><img src="_images/01-editorial.svg" alt="" width="64" height="64"><figcaption>64 px</figcaption></figure>
  <figure><img src="_images/01-editorial.svg" alt="" width="32" height="32"><figcaption>32 px</figcaption></figure>
  <figure><img src="_images/01-editorial.svg" alt="" width="16" height="16"><figcaption>16 px</figcaption></figure>
</div>
::::
:::::

---

## Previous direction — Code tile

:::::{grid} 1 2 2 2
:gutter: 3

::::{grid-item}
:::{image} ../design/logo-prototypes/02-code-tile.svg
:alt: Code tile fr. monogram
:width: 280px
:align: center
:::
::::

::::{grid-item}
A monospaced lettermark held inside a compact charcoal tile. It is the most immediately useful favicon and package avatar, with a slightly stronger developer-tool character.

{download}`Open the standalone SVG <../design/logo-prototypes/02-code-tile.svg>`

### Scale test

<div class="logo-scale-test" aria-label="Code tile mark at several sizes">
  <figure><img src="_images/02-code-tile.svg" alt="" width="64" height="64"><figcaption>64 px</figcaption></figure>
  <figure><img src="_images/02-code-tile.svg" alt="" width="32" height="32"><figcaption>32 px</figcaption></figure>
  <figure><img src="_images/02-code-tile.svg" alt="" width="16" height="16"><figcaption>16 px</figcaption></figure>
</div>
::::
:::::

---

## Previous direction — Constructed ligature

:::::{grid} 1 2 2 2
:gutter: 3

::::{grid-item}
:::{image} ../design/logo-prototypes/03-constructed.svg
:alt: Constructed fr. monogram
:width: 280px
:align: center
:::
::::

::::{grid-item}
A custom geometric mark that joins the crossbar of the *f* to the stem of the *r*. It is the most ownable direction, but also the furthest from familiar typography.

{download}`Open the standalone SVG <../design/logo-prototypes/03-constructed.svg>`

### Scale test

<div class="logo-scale-test" aria-label="Constructed mark at several sizes">
  <figure><img src="_images/03-constructed.svg" alt="" width="64" height="64"><figcaption>64 px</figcaption></figure>
  <figure><img src="_images/03-constructed.svg" alt="" width="32" height="32"><figcaption>32 px</figcaption></figure>
  <figure><img src="_images/03-constructed.svg" alt="" width="16" height="16"><figcaption>16 px</figcaption></figure>
</div>
::::
:::::
