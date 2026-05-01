-- Auto-generated entity for FIR4Tap
-- Do not edit — regenerate via VhdlGenerator.generate_entity()

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library work;
use work.fixed_point_pkg.all;

entity fir4tap is
    port (
        x0           : in  signed(15 downto 0);
        x1           : in  signed(15 downto 0);
        x2           : in  signed(15 downto 0);
        x3           : in  signed(15 downto 0);
        y            : out signed(16 downto 0)
    );
end entity fir4tap;

architecture rtl of fir4tap is

    -- fp_format_t constants (int_bits, frac_bits, is_signed, offset)
    constant FMT_X    : fp_format_t := (1, 15, true,  0.0);
    constant FMT_COEF : fp_format_t := (1, 15, false, 0.0);
    constant FMT_MUL  : fp_format_t := (3, 30, true,  0.0);
    constant FMT_SUM4 : fp_format_t := (5, 30, true,  0.0);
    constant FMT_Y    : fp_format_t := (2, 15, true,  0.0);

    -- Box-filter coefficient: 0.25 in U1.15
    constant C_COEF : unsigned(15 downto 0) :=
        to_unsigned(8192, 16);

    -- Exact-precision intermediates
    signal m0, m1, m2, m3 : signed(32 downto 0);
    signal s01, s23       : signed(33 downto 0);
    signal acc            : signed(34 downto 0);

begin

    -- Multiply each tap by 0.25 (exact: no rounding needed here)
    m0 <= fp_mul_su(x0, C_COEF);
    m1 <= fp_mul_su(x1, C_COEF);
    m2 <= fp_mul_su(x2, C_COEF);
    m3 <= fp_mul_su(x3, C_COEF);

    -- Pairwise accumulation (exact)
    s01 <= fp_add_s(m0, m1);
    s23 <= fp_add_s(m2, m3);
    acc <= fp_add_s(s01, s23);

    -- Requantize to output format (truncate + saturate)
    quantize: process(acc)
        variable tmp : signed(16 downto 0);
        variable sta : fp_status_t;
    begin
        fp_quantize_s(acc, FMT_SUM4, FMT_Y, TRUNCATE, SAT_SATURATE, tmp, sta);
        y <= tmp;
    end process quantize;

end architecture rtl;
