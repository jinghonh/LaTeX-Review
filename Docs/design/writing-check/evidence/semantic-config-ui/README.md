# Semantic configuration UI evidence

The captures use the same production `review_semantic.js` panel, fixed local API state, configured model (`deepseek-chat`), task preview, viewport (`1280x720`), light theme, locale (`en-US`) and timezone (`UTC`). No provider or manuscript request was made.

- **Before**: baseline revision `62430669fc055803b31407be6be0a5bc8aa95519`, [before.png](before.png). The panel showed the model and budget but did not show the effective provider or thinking mode.
- **After**: ticket commit `4aaa8bad1b8695905736f52cfade54aa55b7a454`, [after.png](after.png). The panel shows `provider: deepseek` and `thinking: disabled` beside the budget.
