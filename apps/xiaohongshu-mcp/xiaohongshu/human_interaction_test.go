package xiaohongshu

import (
	"testing"
	"time"

	"github.com/go-rod/rod/lib/proto"
	"github.com/stretchr/testify/assert"
)

func TestRandomDuration(t *testing.T) {
	minDelay := 20 * time.Millisecond
	maxDelay := 40 * time.Millisecond
	for range 100 {
		delay := randomDuration(minDelay, maxDelay)
		assert.GreaterOrEqual(t, delay, minDelay)
		assert.Less(t, delay, maxDelay)
	}

	assert.Equal(t, minDelay, randomDuration(minDelay, minDelay))
	assert.Equal(t, minDelay, randomDuration(minDelay, 10*time.Millisecond))
}

func TestBezierPointEndpoints(t *testing.T) {
	start := proto.Point{X: 10, Y: 20}
	control1 := proto.Point{X: 30, Y: 80}
	control2 := proto.Point{X: 70, Y: 40}
	end := proto.Point{X: 100, Y: 120}

	assert.Equal(t, start, bezierPoint(start, control1, control2, end, 0))
	assert.Equal(t, end, bezierPoint(start, control1, control2, end, 1))
}
