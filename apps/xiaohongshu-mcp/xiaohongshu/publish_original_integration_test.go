//go:build integration

package xiaohongshu

import (
	"testing"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/stretchr/testify/require"
)

func TestConfirmOriginalDeclarationCheckboxOutsideFooter(t *testing.T) {
	controlURL := launcher.New().Headless(true).MustLaunch()
	browser := rod.New().ControlURL(controlURL).MustConnect()
	defer browser.MustClose()

	page := browser.MustPage("about:blank")
	page.MustSetDocumentContent(`
		<!doctype html>
		<style>
			body { margin: 0; font-family: sans-serif; }
			.d-dialog { position: absolute; left: 150px; top: 80px; width: 420px; padding: 24px; background: white; }
			.d-checkbox { display: flex; align-items: center; width: 260px; height: 44px; }
			.d-checkbox input { width: 20px; height: 20px; }
			.footer { margin-top: 36px; }
			button { width: 160px; height: 44px; }
		</style>
		<div class="d-dialog" role="dialog">
			<div class="body">
				<h2>声明原创</h2>
				<label class="d-checkbox">
					<input type="checkbox">
					<span>我已阅读并同意原创声明须知</span>
				</label>
			</div>
			<div class="footer">
				<button class="custom-button" onclick="document.body.dataset.confirmed='true'">声明原创</button>
			</div>
		</div>
	`)

	require.NoError(t, confirmOriginalDeclaration(page))

	checked := page.MustElement(`input[type="checkbox"]`).MustProperty("checked").Bool()
	require.True(t, checked)
	confirmed := page.MustElement("body").MustAttribute("data-confirmed")
	require.NotNil(t, confirmed)
	require.Equal(t, "true", *confirmed)
}
