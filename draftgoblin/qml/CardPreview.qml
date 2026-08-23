import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    property var recommendation: null
    property bool loading: false
    property var imageState: null
    readonly property bool imageLoading: Boolean(
        loading || imageState && imageState.phase === "loading"
    )
    readonly property bool imageAvailable: Boolean(
        recommendation
            && recommendation.card.image_path
            && imageState
            && imageState.phase === "ready"
    )

    color: Theme.surfaceLow
    border.color: Theme.outline
    border.width: 1
    radius: Theme.radius
    Accessible.role: Accessible.Pane
    Accessible.name: recommendation ? "Selected card, " + recommendation.card.name : "Card details"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.panelPadding
        spacing: 12

        Label {
            text: "SELECTED CARD"
            color: Theme.textMuted
            font.pixelSize: 10
            font.bold: true
            font.letterSpacing: 1.2
        }

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(240, root.width - 32)
            Layout.preferredHeight: Layout.preferredWidth * 1.4
            color: "#171817"
            border.color: root.imageLoading ? Theme.warning : Theme.outline
            border.width: 2
            radius: 10
            clip: true

            Image {
                id: cardImage
                anchors.fill: parent
                source: root.imageAvailable ? root.recommendation.card.image_path : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                visible: status === Image.Ready
            }

            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - 32
                spacing: 10
                visible: !cardImage.visible

                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    radius: 24
                    color: root.recommendation && root.recommendation.card.colors.length > 0
                        ? Theme.colorForMana(root.recommendation.card.colors[0]) : Theme.surfaceHigh
                    opacity: 0.85
                }

                Label {
                    Layout.fillWidth: true
                    text: {
                        if (root.imageLoading)
                            return "Loading card image"
                        if (root.recommendation)
                            return root.recommendation.card.name
                        return "No card selected"
                    }
                    color: Theme.text
                    font.pixelSize: 16
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Label {
                    Layout.fillWidth: true
                    text: {
                        if (cardImage.status === Image.Error)
                            return "Card image could not be displayed."
                        if (root.imageState)
                            return root.imageState.message
                        return "Card image unavailable"
                    }
                    color: Theme.textMuted
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }
        }

        ColumnLayout {
            visible: root.recommendation !== null
            Layout.fillWidth: true
            spacing: 6

            Label {
                Layout.fillWidth: true
                text: root.recommendation ? root.recommendation.card.name : ""
                color: Theme.text
                font.pixelSize: 18
                font.bold: true
                wrapMode: Text.WordWrap
            }

            Label {
                Layout.fillWidth: true
                text: root.recommendation
                    ? root.recommendation.card.types.join(" · ")
                        + "   ·   MV " + root.recommendation.card.mana_value
                    : ""
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true

                Label {
                    text: root.recommendation ? "DG " + root.recommendation.score : ""
                    color: Theme.primary
                    font.family: "monospace"
                    font.bold: true
                }

                Label {
                    text: root.recommendation && root.recommendation.win_rate !== null
                        ? "17L " + (root.recommendation.win_rate * 100).toFixed(1) + "%" : "17L —"
                    color: Theme.textMuted
                    font.family: "monospace"
                }

                Label {
                    text: root.recommendation ? root.recommendation.letter_grade || "—" : ""
                    color: Theme.warning
                    font.bold: true
                }
            }

            Label {
                Layout.fillWidth: true
                text: root.recommendation ? root.recommendation.explanation || "" : ""
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }

        Item {
            Layout.fillHeight: true
        }
    }
}

