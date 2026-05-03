/* TJpgDec configuration for MicroPython natmod */

#define JD_SZBUF        512     /* Stream input buffer size */
#define JD_FORMAT        1      /* Output: 1 = RGB565 (matches ST7789) */
#define JD_USE_SCALE     1      /* Enable 1/2, 1/4, 1/8 descaling */
#define JD_TBLCLIP       0      /* Disable table saturation (saves 1KB) */
#define JD_FASTDECODE    1      /* Barrel-shift optimization, 3500B workspace */

#if JD_FASTDECODE == 0
 #define TJPGD_WORKSPACE_SIZE 3100
#elif JD_FASTDECODE == 1
 #define TJPGD_WORKSPACE_SIZE 3500
#else
 #define TJPGD_WORKSPACE_SIZE 9644
#endif
