import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

FocusScope {
    id: root

    required property var recommendation
    required property bool selected
    required property bool wide
    required property bool secondaryStats
    signal chosen(int grpId)

    implicitHeight: wide ? 48 : 70
    Accessible.role: Accessible.ListItem
    Accessible.name: "Rank " + recommendation.rank + ", " + recommendation.card.name
    Accessible.description: "Press Enter or Space to focus this recommendation."
    activeFocusOnTab: true
    Keys.onReturnPressed: chosen(recommendation.card.grp_id)
    Keys.onSpacePressed: chosen(recommendation.card.grp_id)

    Rectangle {
        anchors.fill: parent
        color: root.selected
            ? Theme.surfaceHighest
            : root.recommendation.rank === 1 ? "#26300f" : Theme.surface
        border.color: root.activeFocus
            ? Theme.focus
            : root.recommendation.rank === 1 ? Theme.primary : Theme.outline
        border.width: root.activeFocus || root.recommendation.rank === 1 ? 2 : 1
        radius: Theme.radius
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            root.forceActiveFocus()
            root.chosen(root.recommendation.card.grp_id)
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 10

        Label {
            Layout.preferredWidth: 26
            text: recommendation.rank
            color: recommendation.rank === 1 ? Theme.primary : Theme.textMuted
            font.family: "monospace"
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Label {
                    Layout.fillWidth: true
                    text: recommendation.card.name
                    color: Theme.text
                    font.bold: recommendation.rank === 1
                    elide: Text.ElideRight
                }

                Label {
                    visible: recommendation.rank === 1
                    text: "RECOMMENDED"
                    color: Theme.primary
                    font.pixelSize: 9
                    font.bold: true
                    font.letterSpacing: 0.8
                }
            }

            Label {
                visible: !root.wide
                Layout.fillWidth: true
                text: (root.recommendation.card.colors.length > 0
                    ? root.recommendation.card.colors.join(" · ") : "Colorless")
                    + "   ·   " + root.recommendation.color_fit
                    + (root.secondaryStats
                        ? "   ·   ALSA " + (root.recommendation.average_last_seen_at !== null
                            && root.recommendation.average_last_seen_at !== undefined
                            ? root.recommendation.average_last_seen_at.toFixed(2) : "—")
                        : "")
                color: Theme.textMuted
                font.pixelSize: 11
                elide: Text.ElideRight
            }
        }

        Label {
            visible: root.wide
            Layout.preferredWidth: 64
            text: recommendation.card.colors.length > 0
                ? recommendation.card.colors.join(" · ") : "—"
            color: recommendation.card.colors.length > 0
                ? Theme.colorForMana(recommendation.card.colors[0]) : Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            Layout.preferredWidth: 52
            text: recommendation.score
            color: Theme.text
            font.family: "monospace"
            font.pixelSize: 16
            font.bold: true
            horizontalAlignment: Text.AlignRight
        }

        Label {
            visible: root.secondaryStats
            Layout.preferredWidth: 58
            text: recommendation.win_rate !== null
                ? (recommendation.win_rate * 100).toFixed(1) + "%" : "—"
            color: Theme.textMuted
            font.family: "monospace"
            horizontalAlignment: Text.AlignRight
        }
        Label {
            Layout.preferredWidth: 34
            text: recommendation.letter_grade || "—"
            color: Theme.warning
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            visible: root.wide
            Layout.preferredWidth: 84
            text: recommendation.color_fit
            color: recommendation.color_fit === "Off color" ? Theme.warning : Theme.textMuted
            horizontalAlignment: Text.AlignRight
        }
    }
}

