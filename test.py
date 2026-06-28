import requests

cookies = {
    'buvid3': 'D31240F4-DE75-412C-2C78-D8379D36685740979infoc',
    'b_nut': '1782398440',
    '_uuid': '72B98DCC-D8AE-FE5A-C87C-875FD61010543A41024infoc',
    'home_feed_column': '5',
    'buvid_fp': 'deefa477a1a6f663278e2f9d7e357642',
    'buvid4': 'A758CDFE-2CF2-E805-BD07-A225684CDDA641984-026062522-QfJgneUpwI0T2ebEZfCvlA%3D%3D',
    'CURRENT_FNVAL': '2000',
    'browser_resolution': '1710-651',
    'bili_ticket': 'eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODI2NTc2OTIsImlhdCI6MTc4MjM5ODQzMiwicGx0IjotMX0.DHjY3c1M2SEQKdVthKFL4dAjjR3ZDscNqh3y7ek0zJo',
    'bili_ticket_expires': '1782657632',
    'sid': 'n3pz0v7z',
    'b_lsid': '73345EF0_19EFF3B0E99',
}

headers = {
    'accept': '*/*',
    'accept-language': 'zh-CN,zh;q=0.9',
    'origin': 'https://space.bilibili.com',
    'priority': 'u=1, i',
    'referer': 'https://space.bilibili.com/3706959876327428/dynamic',
    'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    'cookie': 'buvid3=D31240F4-DE75-412C-2C78-D8379D36685740979infoc; b_nut=1782398440; _uuid=72B98DCC-D8AE-FE5A-C87C-875FD61010543A41024infoc; home_feed_column=5; buvid_fp=deefa477a1a6f663278e2f9d7e357642; buvid4=A758CDFE-2CF2-E805-BD07-A225684CDDA641984-026062522-QfJgneUpwI0T2ebEZfCvlA%3D%3D; CURRENT_FNVAL=2000; browser_resolution=1710-651; bili_ticket=eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODI2NTc2OTIsImlhdCI6MTc4MjM5ODQzMiwicGx0IjotMX0.DHjY3c1M2SEQKdVthKFL4dAjjR3ZDscNqh3y7ek0zJo; bili_ticket_expires=1782657632; sid=n3pz0v7z; b_lsid=73345EF0_19EFF3B0E99',
}

response = requests.get(
    'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?offset=&host_mid=3706959876327428&timezone_offset=-480&platform=web&features=itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,forwardListHidden,decorationCard,commentsNewVersion,onlyfansAssetsV2,ugcDelete,onlyfansQaCard,avatarAutoTheme,sunflowerStyle,cardsEnhance,eva3CardOpus,eva3CardVideo,eva3CardComment,eva3CardUser&web_location=333.1387&dm_img_list=[]&dm_img_str=V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ&dm_cover_img_str=QU5HTEUgKEFwcGxlLCBBTkdMRSBNZXRhbCBSZW5kZXJlcjogQXBwbGUgTTQsIFVuc3BlY2lmaWVkIFZlcnNpb24pR29vZ2xlIEluYy4gKEFwcGxlKQ&dm_img_inter=%7B%22ds%22:[],%22wh%22:[4776,6207,18],%22of%22:[327,654,327]%7D&x-bili-device-req-json=%7B%22platform%22:%22web%22,%22device%22:%22pc%22,%22spmid%22:%22333.1387%22%7D&w_rid=fabb78d1a42d61bca4c7c42baab8f322&wts=1782398521',
    cookies=cookies,
    headers=headers,
)
print(response.json())
