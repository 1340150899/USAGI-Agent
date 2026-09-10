package xiaohongshu

import (
	"math"
	"math/rand"
	"runtime"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/input"
	"github.com/go-rod/rod/lib/proto"
	"github.com/pkg/errors"
)

const (
	humanTypeMinDelay = 25 * time.Millisecond
	humanTypeMaxDelay = 75 * time.Millisecond
)

func randomDuration(minDelay, maxDelay time.Duration) time.Duration {
	if maxDelay <= minDelay {
		return minDelay
	}
	return minDelay + time.Duration(rand.Int63n(int64(maxDelay-minDelay)))
}

func humanPause(minDelay, maxDelay time.Duration) {
	time.Sleep(randomDuration(minDelay, maxDelay))
}

// humanClick 模拟鼠标移动、停顿、按下和松开。
func humanClick(page *rod.Page, elem *rod.Element) error {
	box, err := humanElementBox(page, elem)
	if err != nil {
		return err
	}

	paddingX := math.Min(box.Width*0.25, 12)
	paddingY := math.Min(box.Height*0.25, 8)
	target := proto.Point{
		X: box.X + paddingX + rand.Float64()*(box.Width-2*paddingX),
		Y: box.Y + paddingY + rand.Float64()*(box.Height-2*paddingY),
	}
	return humanClickPoint(page, target)
}

func humanClickAtRatio(page *rod.Page, elem *rod.Element, xRatio, yRatio float64) error {
	box, err := humanElementBox(page, elem)
	if err != nil {
		return err
	}

	xRatio = math.Max(0.1, math.Min(0.9, xRatio+(rand.Float64()-0.5)*0.06))
	yRatio = math.Max(0.1, math.Min(0.9, yRatio+(rand.Float64()-0.5)*0.06))
	return humanClickPoint(page, proto.Point{
		X: box.X + box.Width*xRatio,
		Y: box.Y + box.Height*yRatio,
	})
}

func humanElementBox(page *rod.Page, elem *rod.Element) (*proto.DOMRect, error) {
	if err := humanScrollIntoView(page, elem); err != nil {
		return nil, err
	}
	if err := elem.WaitVisible(); err != nil {
		return nil, errors.Wrap(err, "等待元素可见失败")
	}
	if err := elem.WaitEnabled(); err != nil {
		return nil, errors.Wrap(err, "等待元素可点击失败")
	}

	shape, err := elem.Shape()
	if err != nil {
		return nil, errors.Wrap(err, "获取元素位置失败")
	}
	box := shape.Box()
	if box == nil || box.Width <= 0 || box.Height <= 0 {
		return nil, errors.New("元素没有可点击区域")
	}
	return box, nil
}

// humanScrollIntoView 使用真实滚轮逐步将元素移入可视区域。
func humanScrollIntoView(page *rod.Page, elem *rod.Element) error {
	viewportWidth, viewportHeight, err := viewportSize(page)
	if err != nil {
		return err
	}

	// 发布页等固定高度布局中，页面本身不可滚动，
	// 底部固定栏内的元素中心可能永远进不了 48px 边距带。
	// 此时放宽边距：元素中心在视口内即视为可见。
	margin := 48.0
	if docHeight, dhErr := page.Eval(`() => Math.max(
		document.documentElement.scrollHeight,
		document.body ? document.body.scrollHeight : 0)`); dhErr == nil {
		if float64(docHeight.Value.Int()) <= viewportHeight+2 {
			margin = 4.0
		}
	}

	for attempt := 0; attempt < 10; attempt++ {
		shape, shapeErr := elem.Shape()
		if shapeErr != nil {
			return errors.Wrap(shapeErr, "获取待滚动元素位置失败")
		}
		box := shape.Box()
		if box == nil {
			return errors.New("待滚动元素没有可见区域")
		}

		centerX := box.X + box.Width/2
		centerY := box.Y + box.Height/2
		if centerX >= 0 && centerX <= viewportWidth &&
			centerY >= margin && centerY <= viewportHeight-margin {
			return nil
		}

		delta := centerY - viewportHeight*0.55
		maxDelta := viewportHeight * 0.82
		delta = math.Max(-maxDelta, math.Min(maxDelta, delta))
		if math.Abs(delta) < 120 {
			if delta < 0 {
				delta = -120
			} else {
				delta = 120
			}
		}
		if err := humanScrollBy(page, delta); err != nil {
			return errors.Wrap(err, "滚动元素到可视区域失败")
		}
	}

	return errors.New("多次滚轮滚动后元素仍不在可视区域")
}

// humanScrollBy 在页面中央附近移动鼠标并滚动真实滚轮。
func humanScrollBy(page *rod.Page, deltaY float64) error {
	if deltaY == 0 {
		return nil
	}

	viewportWidth, viewportHeight, err := viewportSize(page)
	if err != nil {
		return err
	}
	target := proto.Point{
		X: viewportWidth * (0.46 + rand.Float64()*0.12),
		Y: viewportHeight * (0.42 + rand.Float64()*0.16),
	}
	if err := humanMoveMouse(page, target); err != nil {
		return errors.Wrap(err, "移动鼠标到滚动区域失败")
	}

	humanPause(70*time.Millisecond, 180*time.Millisecond)
	steps := 4 + rand.Intn(5)
	if math.Abs(deltaY) > viewportHeight {
		steps += 2
	}
	if err := page.Mouse.Scroll(0, deltaY, steps); err != nil {
		return errors.Wrap(err, "滚动鼠标滚轮失败")
	}
	humanPause(140*time.Millisecond, 360*time.Millisecond)
	return nil
}

func viewportSize(page *rod.Page) (float64, float64, error) {
	widthResult, err := page.Eval(`() => window.innerWidth`)
	if err != nil {
		return 0, 0, errors.Wrap(err, "获取页面宽度失败")
	}
	heightResult, err := page.Eval(`() => window.innerHeight`)
	if err != nil {
		return 0, 0, errors.Wrap(err, "获取页面高度失败")
	}

	width := float64(widthResult.Value.Int())
	height := float64(heightResult.Value.Int())
	if width <= 0 || height <= 0 {
		return 0, 0, errors.New("页面可视区域尺寸无效")
	}
	return width, height, nil
}

func humanClickPoint(page *rod.Page, target proto.Point) error {
	if err := humanMoveMouse(page, target); err != nil {
		return errors.Wrap(err, "移动鼠标到元素失败")
	}

	humanPause(80*time.Millisecond, 220*time.Millisecond)
	if err := page.Mouse.Down(proto.InputMouseButtonLeft, 1); err != nil {
		return errors.Wrap(err, "按下鼠标失败")
	}
	humanPause(45*time.Millisecond, 110*time.Millisecond)
	if err := page.Mouse.Up(proto.InputMouseButtonLeft, 1); err != nil {
		return errors.Wrap(err, "松开鼠标失败")
	}
	humanPause(120*time.Millisecond, 320*time.Millisecond)
	return nil
}

// humanMoveMouse 使用带轻微弧度的轨迹移动鼠标。
func humanMoveMouse(page *rod.Page, target proto.Point) error {
	start := page.Mouse.Position()
	dx := target.X - start.X
	dy := target.Y - start.Y
	distance := math.Hypot(dx, dy)
	if distance < 1 {
		return page.Mouse.MoveTo(target)
	}

	curve := (rand.Float64()*0.24 - 0.12) * distance
	perpendicularX := -dy / distance
	perpendicularY := dx / distance
	control1 := proto.Point{
		X: start.X + dx*0.30 + perpendicularX*curve,
		Y: start.Y + dy*0.30 + perpendicularY*curve,
	}
	control2 := proto.Point{
		X: start.X + dx*0.72 - perpendicularX*curve*0.35,
		Y: start.Y + dy*0.72 - perpendicularY*curve*0.35,
	}

	steps := int(distance/45) + 8 + rand.Intn(5)
	if steps > 28 {
		steps = 28
	}
	for i := 1; i <= steps; i++ {
		t := float64(i) / float64(steps)
		if err := page.Mouse.MoveTo(bezierPoint(start, control1, control2, target, t)); err != nil {
			return err
		}
		humanPause(5*time.Millisecond, 14*time.Millisecond)
	}
	return nil
}

func bezierPoint(start, control1, control2, end proto.Point, t float64) proto.Point {
	oneMinusT := 1 - t
	return proto.Point{
		X: oneMinusT*oneMinusT*oneMinusT*start.X +
			3*oneMinusT*oneMinusT*t*control1.X +
			3*oneMinusT*t*t*control2.X +
			t*t*t*end.X,
		Y: oneMinusT*oneMinusT*oneMinusT*start.Y +
			3*oneMinusT*oneMinusT*t*control1.Y +
			3*oneMinusT*t*t*control2.Y +
			t*t*t*end.Y,
	}
}

// humanFocusAndType 先点击获得焦点，再逐字输入。
func humanFocusAndType(page *rod.Page, elem *rod.Element, text string) error {
	if err := humanClick(page, elem); err != nil {
		return err
	}
	return humanType(page, text)
}

// humanReplaceText 使用键盘全选删除后逐字输入。
func humanReplaceText(page *rod.Page, elem *rod.Element, text string) error {
	if err := humanClick(page, elem); err != nil {
		return err
	}

	modifier := input.ControlLeft
	if runtime.GOOS == "darwin" {
		modifier = input.MetaLeft
	}
	if err := page.KeyActions().Press(modifier).Type('a').Release(modifier).Type(input.Backspace).Do(); err != nil {
		return errors.Wrap(err, "清空输入框失败")
	}
	humanPause(80*time.Millisecond, 180*time.Millisecond)
	return humanType(page, text)
}

// humanType 通过键盘或输入法逐字输入当前焦点元素。
func humanType(page *rod.Page, text string) error {
	for _, char := range text {
		var err error
		switch {
		case char == '\n':
			err = page.Keyboard.Type(input.Enter)
		case char >= 32 && char <= 126:
			err = page.Keyboard.Type(input.Key(char))
		default:
			err = page.InsertText(string(char))
		}
		if err != nil {
			return errors.Wrapf(err, "输入字符[%c]失败", char)
		}

		minDelay, maxDelay := humanTypeMinDelay, humanTypeMaxDelay
		if char == '，' || char == '。' || char == '！' || char == '？' || char == '\n' {
			minDelay, maxDelay = 90*time.Millisecond, 190*time.Millisecond
		}
		humanPause(minDelay, maxDelay)
	}
	return nil
}
