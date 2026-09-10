var Starfield = function() {
	
	var that = {};
	var internal = {};
	var state = {
		canvas:		null,
		ctx:		null,
		size:		null,
		
		playable:	false,
		play:		false,
		timer:		null, 
		
		stars:		[],
		maxStars:	100,
		
		ie:			false,
		fixed:		false
	};
	
	internal.fixedSupport = function() {
		var c = document.body;
		
		if (document.createElement && c && c.appendChild && c.removeChild) {
			var el = document.createElement('div');
			if (!el.getBoundingClientRect) return null;
			
			el.innerHTML = 'x';
			el.style.cssText = 'position:fixed;top:100px;';
			c.appendChild(el);
			
			var originalHeight = c.style.height,
			originalScrollTop = c.scrollTop;
			
			c.style.height = '3000px';
			c.scrollTop = 500;
			
			var elementTop = el.getBoundingClientRect().top;
			c.style.height = originalHeight;
			
			var isSupported = (elementTop === 100);
			c.removeChild(el);
			c.scrollTop = originalScrollTop;
			return isSupported;
		}
		return null;
	};
	
	internal.getSize = function() {
		var del = document.documentElement;
		return [del.clientWidth, del.clientHeight];
	};
	
	internal.initCanvas = function(canvas) {
		if (typeof FlashCanvas != "undefined") {
			FlashCanvas.initElement(canvas);
			state.ie = true;
		}
		return canvas;
	};
	
	internal.genStar = function(index, start) {
		var opacity = 0;
		if (!!start) {
			opacity = Math.random();
		}
		state.stars[index] = [state.size[0] * Math.random(), 
							  state.size[1] * Math.random(), 
							  1 + Math.random(), 
							  opacity];
	};
	
	internal.draw = function() {
		// state.ctx.fillStyle = "rgba(0,0,0,0.4)";
		// state.ctx.fillRect(0, 0, state.size[0], state.size[1]);
		state.ctx.clearRect(0, 0, state.size[0], state.size[1]);
		
		var star 	= null,
			sh 		= function(index) { return star[index] - state.size[index]/2; },
			starlen = state.stars.length;
		
		for (var j = 0; j < starlen; j++) {
			var star = state.stars[j],
				fgcolour = "rgba(255,255,255,"+star[3]+")";
			
			state.ctx.fillStyle = fgcolour;
			state.ctx.fillRect(star[0]-1, star[1]-1, 2, 2);
			
			var angle = Math.atan2(sh(0), sh(1));
			state.stars[j][0] += (star[2] * Math.sin(angle)); 
			state.stars[j][1] += (star[2] * Math.cos(angle));
			
			if (star[0] < 0 || star[0] > state.size[0] || star[1] < 0 || star[1] > state.size[1]) {
				internal.genStar(j);
			}
			else
			{
				state.stars[j][2] += 0.02;
				
				if (state.stars[j][3] < 1) {
					state.stars[j][3] += 0.015;					
					if (state.stars[j][3] > 1) {
						state.stars[j][3] = 1;
					}
				}
			}
		}
	};
	
	internal.load = function() {
		state.size = internal.getSize();
	
		state.canvas = document.createElement('canvas');
		state.canvas.id 		= 'starfield';
		state.canvas.width 		= state.size[0];
		state.canvas.height 	= state.size[1];
		
		if (!!state.fixed) {
			state.canvas.style.position = 'fixed';
		}
		else {
			state.canvas.style.position = 'absolute';
			$(window).scroll(function() {
				if ($('canvas').is(':visible')) {
					$(state.canvas).top($(window).scrollTop());
				}
			});
		}
		state.canvas.style.top = '0px';
		state.canvas.style.left = '0px';
		document.getElementsByTagName('body')[0].appendChild(state.canvas);
		
		state.canvas = internal.initCanvas(document.getElementById('starfield'));
		
		try {
			state.ctx = state.canvas.getContext('2d');
			state.play = true;
		}
		catch(ex) {
			alert(ex);
		}
		
		for (var i = 0; i < state.maxStars; i++) {
			internal.genStar(i, true);
		}
		
		/*@cc_on
		state.ie = true;
		@*/
		if (state.ie) {
			document.onfocusout = that.blur;
			document.onfocusin 	= that.focus;
		} 
		else {
			window.onblur 		= that.blur;
			window.onfocus 		= that.focus;
		}
	};
	
	internal.setInterval = function() {
		var run = function(){
			if (!!state.playable && !!state.play) {
				internal.draw();
			}
		};
		state.timer = setInterval(run, 40);
	};
	
	internal.setupResize = function() {
		var NewSize = internal.getSize();
		var SizeFactor = [NewSize[0] / state.size[0], NewSize[1] / state.size[1]];
		
		for (var j in state.stars) {
			if (j == 'length') continue;
			state.stars[j][0] *= SizeFactor[0];
			state.stars[j][1] *= SizeFactor[1];
		}
		
		state.size 			= NewSize;
		state.canvas.width 	= state.size[0];
		state.canvas.height = state.size[1];
	};
	
	that.focus = function() {
		that.play();
	};
	
	that.blur = function() {
		that.stop();
	};
	
	that.play = function() {
		if (!!state.playable) {
			if (!state.play) {
				state.play = true;
			}
			state.canvas.style.display = 'block';
			internal.setInterval();
		}
	};
	
	that.stop = function() {
		state.play = false;
		clearInterval(state.timer);
		state.canvas.style.display = 'none';
	};
	
	that.setPlayable = function(playable) {
		state.playable = !!(playable);
	};
	
	state.fixed = internal.fixedSupport();
	internal.load();
	$(window).resize(internal.setupResize);
	
	return that;	
};