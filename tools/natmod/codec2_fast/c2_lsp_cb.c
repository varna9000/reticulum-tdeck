/* THIS IS A GENERATED FILE. Edit generate_codebook.c and its input */

/*
 * This intermediary file and the files that used to create it are under
 * The LGPL. See the file COPYING.
 */

#include "c2_defines.h"

/* /Users/rozzie/Desktop/codec2/src/codebook/lsp1.txt */
static const float codes0[] = {
    225,
    250,
    275,
    300,
    325,
    350,
    375,
    400,
    425,
    450,
    475,
    500,
    525,
    550,
    575,
    600
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp2.txt */
static const float codes1[] = {
    325,
    350,
    375,
    400,
    425,
    450,
    475,
    500,
    525,
    550,
    575,
    600,
    625,
    650,
    675,
    700
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp3.txt */
static const float codes2[] = {
    500,
    550,
    600,
    650,
    700,
    750,
    800,
    850,
    900,
    950,
    1000,
    1050,
    1100,
    1150,
    1200,
    1250
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp4.txt */
static const float codes3[] = {
    700,
    800,
    900,
    1000,
    1100,
    1200,
    1300,
    1400,
    1500,
    1600,
    1700,
    1800,
    1900,
    2000,
    2100,
    2200
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp5.txt */
static const float codes4[] = {
    950,
    1050,
    1150,
    1250,
    1350,
    1450,
    1550,
    1650,
    1750,
    1850,
    1950,
    2050,
    2150,
    2250,
    2350,
    2450
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp6.txt */
static const float codes5[] = {
    1100,
    1200,
    1300,
    1400,
    1500,
    1600,
    1700,
    1800,
    1900,
    2000,
    2100,
    2200,
    2300,
    2400,
    2500,
    2600
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp7.txt */
static const float codes6[] = {
    1500,
    1600,
    1700,
    1800,
    1900,
    2000,
    2100,
    2200,
    2300,
    2400,
    2500,
    2600,
    2700,
    2800,
    2900,
    3000
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp8.txt */
static const float codes7[] = {
    2300,
    2400,
    2500,
    2600,
    2700,
    2800,
    2900,
    3000
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp9.txt */
static const float codes8[] = {
    2500,
    2600,
    2700,
    2800,
    2900,
    3000,
    3100,
    3200
};
/* /Users/rozzie/Desktop/codec2/src/codebook/lsp10.txt */
static const float codes9[] = {
    2900,
    3100,
    3300,
    3500
};

/* dlsp1.txt — dlsp3.txt (identical: 25..800 step 25) */
static const float dlsp_codes_lin25[] = {
     25,  50,  75, 100, 125, 150, 175, 200,
    225, 250, 275, 300, 325, 350, 375, 400,
    425, 450, 475, 500, 525, 550, 575, 600,
    625, 650, 675, 700, 725, 750, 775, 800
};
/* dlsp4.txt — dlsp6.txt (identical: 25..200 step25 then 250..1400 step50) */
static const float dlsp_codes_wide[] = {
     25,  50,  75, 100, 125, 150, 175, 200,
    250, 300, 350, 400, 450, 500, 550, 600,
    650, 700, 750, 800, 850, 900, 950, 1000,
    1050, 1100, 1150, 1200, 1250, 1300, 1350, 1400
};

const struct lsp_codebook lsp_cbd[] = {
    /* dlsp1.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp2.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp3.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp4.txt */  { 1, 5, 32, dlsp_codes_wide },
    /* dlsp5.txt */  { 1, 5, 32, dlsp_codes_wide },
    /* dlsp6.txt */  { 1, 5, 32, dlsp_codes_wide },
    /* dlsp7.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp8.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp9.txt */  { 1, 5, 32, dlsp_codes_lin25 },
    /* dlsp10.txt */ { 1, 5, 32, dlsp_codes_lin25 },
    { 0, 0, 0, 0 }
};
const struct lsp_codebook lsp_cbjmv[] = {{0,0,0,0}};

const struct lsp_codebook lsp_cb[] = {
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp1.txt */
    {
        1,
        4,
        16,
        codes0
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp2.txt */
    {
        1,
        4,
        16,
        codes1
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp3.txt */
    {
        1,
        4,
        16,
        codes2
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp4.txt */
    {
        1,
        4,
        16,
        codes3
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp5.txt */
    {
        1,
        4,
        16,
        codes4
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp6.txt */
    {
        1,
        4,
        16,
        codes5
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp7.txt */
    {
        1,
        4,
        16,
        codes6
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp8.txt */
    {
        1,
        3,
        8,
        codes7
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp9.txt */
    {
        1,
        3,
        8,
        codes8
    },
    /* /Users/rozzie/Desktop/codec2/src/codebook/lsp10.txt */
    {
        1,
        2,
        4,
        codes9
    },
    { 0, 0, 0, 0 }
};
