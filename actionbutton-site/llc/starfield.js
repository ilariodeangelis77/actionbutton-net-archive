var canvas = null;
var ctx = null;
var Size = null;
var play = false;

var stars = [];
var maxStar = 200;

var GetSize = function()
{
	return [
		document.documentElement.clientWidth,
		document.documentElement.clientHeight
	];
};

window.onload = function()
{
	Size = GetSize();
	
	canvas = document.createElement('canvas');
	canvas.id = 'starfield';
	canvas.width = Size[0];
	canvas.height = Size[1];
	document.getElementsByTagName('body')[0].appendChild(canvas);
	try
	{
		ctx = canvas.getContext('2d');
		play = true;
	}
	catch(ex){}
	
	for(var i = 0; i < maxStar; i++)
	{
		stars[i] = [
			Size[0] * Math.random(),
			Size[1] * Math.random(),
			1 + Math.random(),
			Math.random()
		];
	}
	
	var DrawStars = function()
	{
		ctx.fillStyle = "rgba(0,0,0,0.4)";
		ctx.fillRect(0,0,Size[0],Size[1]);
		//ctx.clearRect(0,0,Size[0],Size[1]);
		for( var j in stars )
		{
			ctx.fillStyle = "rgba(255, 255, 255, " + stars[j][3] + ")";
			ctx.fillRect(
				stars[j][0]-1,
				stars[j][1]-1,
				2,
				2
			);
			var angle = Math.atan2(
				stars[j][0] - (Size[0]/2),
				stars[j][1] - (Size[1]/2)
			);
			stars[j][0] += (stars[j][2] * Math.sin( angle )); 
			stars[j][1] += (stars[j][2] * Math.cos( angle ));
			
			if(stars[j][0] < 0 || stars[j][0] > Size[0] || 
				stars[j][1] < 0 || stars[j][1] > Size[1])
			{
				stars[j] = [
					Size[0] * Math.random(),
					Size[1] * Math.random(),
					1 + Math.random(),
					0
				];
			}
			else
			{
				stars[j][2] += 0.02;
				stars[j][3] += 0.015;
				if( stars[j][3] > 1 )
					stars[j][3] = 1;
			}
		}
		if(play) setTimeout(DrawStars, 40);
	};
	
	if(play) DrawStars();
};

window.onresize = function()
{
	var NewSize = GetSize();
	var SizeFactor = [
		NewSize[0] / Size[0],
		NewSize[1] / Size[1]
	];
	for( var j in stars )
	{
		stars[j][0] *= SizeFactor[0];
		stars[j][1] *= SizeFactor[1];
	}
	Size = NewSize;
	canvas.width = Size[0];
	canvas.height = Size[1];
};